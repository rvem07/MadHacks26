"""Employee clarification workflow: execute, checkpoint, return; resume on answer."""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from difflib import SequenceMatcher
from uuid import uuid4

from .audit import now_iso
from .pipeline import SAMPLE, publish_pipeline, normalize
from .runs import _mlflow_span
from .config import get_settings
from .watchstore import Conflict, open_documents


log = logging.getLogger(__name__)


def tokens(text):
    return set(re.findall(r'[a-z0-9]+', text.lower()))


def model_json(messages):
    from .llm import chat
    result = chat(os.getenv('AFD_WATCHER_MODEL', 'databricks-qwen3-next-80b-a3b-instruct'), messages,
                  max_tokens=1600, temperature=0, timeout_s=90)
    result = re.sub(r'^```(?:json)?\s*|\s*```$', '', result.strip())
    return json.loads(result)


class Watcher:
    def __init__(self, documents, model=None, background=True):
        self.db = documents
        self.background = background
        self.model = model or (model_json if os.getenv('AFD_WATCHER_AGENT', 'local') == 'databricks' else None)
        self.lock = threading.RLock()
        self.workers = {}

    def publish(self, directory=SAMPLE):
        return publish_pipeline(self.db, directory)

    def pipeline(self, batch=None):
        active = self.db.get('active_pipeline')
        if not batch and not active:
            raise ValueError('No published pipeline; run scripts/run_pipeline.py first.')
        pipeline = self.db.get(f'pipeline:{batch or active["batch_id"]}')
        if not pipeline:
            raise ValueError('Unknown pipeline batch')
        return pipeline

    def recover(self):
        # Repair outbox projections before scheduling work. Waiting runs never execute here.
        for doc in self.db.list('pipeline:'):
            self.db.project(doc)
        for doc in self.db.list('run:'):
            self.db.project(doc)
            if doc.get('projection_error'):
                doc = self.commit(doc)
            if doc['status'] in {'running', 'resumed'} and not doc.get('execution_error'):
                self.schedule(doc['run_id'])

    def get(self, run_id):
        doc = self.db.get(f'run:{run_id}')
        if not doc:
            raise KeyError('Unknown run')
        return doc

    def runs(self):
        return sorted(self.db.list('run:'), key=lambda r: r['created_at'], reverse=True)

    def commit(self, doc):
        doc = dict(doc)
        doc.pop('projection_error', None)
        saved = self.db.put(doc, doc.get('revision', 0))
        try:
            self.db.project(saved)
        except Exception as exc:
            log.exception('Checkpoint committed but query-table projection failed for %s', saved['id'])
            failed = dict(saved, projection_error=f'{type(exc).__name__}: {exc}')
            try:
                self.db.put(failed, saved['revision'])
            except Exception:
                log.exception('Could not persist projection diagnostic; durable checkpoint remains authoritative')
            raise
        return saved

    def event(self, doc, action, summary, *, actor='agent', target=None, edge=None, result=None, rationale=None):
        step = len(doc['events']) + 1
        event = {'event_id': f'{doc["run_id"]}:{step}', 'run_id': doc['run_id'], 'step': step, 'seq': step,
                 'actor': actor, 'action': action, 'tool': action, 'target_node': target, 'edge': edge,
                 'result_summary': summary, 'state': doc['status'], 'timestamp': now_iso(),
                 'result': result or {}, 'rationale': rationale,
                 'fidelity': {'result': 'reported' if action == 'conclude' else 'observed', 'rationale': 'reported'} }
        doc['events'].append(event)
        return event

    def start(self, task, requester, scenario=None):
        task = task.strip()
        if not task:
            raise ValueError('Task is required')
        pipeline = self.pipeline()
        if scenario:
            from .wildlife import SCENARIO, STAGES
            if scenario != SCENARIO:
                raise ValueError('Unknown demo scenario')
            missing = {stage['person'] for stage in STAGES} - {p['id'] for p in pipeline['nodes']}
            if missing:
                raise ValueError('The demo needs its existing employee nodes: ' + ', '.join(sorted(missing)))
        rid = uuid4().hex
        doc = {'id': f'run:{rid}', 'run_id': rid, 'task': task, 'requester': requester,
               'batch_id': pipeline['batch_id'], 'status': 'running', 'created_at': now_iso(),
               'requests': [], 'events': [], 'messages': [{'role': 'user', 'content': task}],
               'answer': None, 'execution_error': None, 'route': None,
               'scenario': scenario, 'scenario_decisions': {}}
        self.event(doc, 'start', f'Started task: {task}', actor='system')
        self.commit(doc)
        self.schedule(rid)
        return self.get(rid)

    def schedule(self, run_id):
        if not self.background:
            self.execute(run_id)
            return
        with self.lock:
            worker = self.workers.get(run_id)
            if worker and worker.is_alive():
                return
            worker = threading.Thread(target=self._work, args=(run_id,), name=f'watcher-{run_id}', daemon=True)
            self.workers[run_id] = worker
            worker.start()

    def _work(self, run_id):
        try:
            self.execute(run_id)
        finally:
            with self.lock:
                self.workers.pop(run_id, None)
                # An answer can arrive immediately after a checkpoint, before this worker exits.
                doc = self.get(run_id)
                if doc['status'] in {'running', 'resumed'} and not doc.get('execution_error'):
                    self.schedule(run_id)

    def who_should_i_ask(self, phrasing, pipeline, scenario=None):
        if scenario:
            from .wildlife import STAGES
            owner = next(p for p in pipeline['nodes'] if p['id'] == STAGES[0]['person'])
            return {'topic': 'Secure wildlife-protection application', 'owner': owner, 'chain': [owner],
                    'confidence': 1.0, 'source': ['guided wildlife scenario v1'], 'method': 'explicit scenario routing'}
        responsibilities = pipeline['silver_responsibilities']
        exact = [r for r in responsibilities if normalize(r['topic']) == normalize(phrasing)]
        scored = sorted(((len(tokens(phrasing) & tokens(r['topic'])) / max(1, len(tokens(r['topic']))),
                          SequenceMatcher(None, normalize(phrasing), r['topic']).ratio(), r)
                         for r in responsibilities), key=lambda x: (-x[0], -x[1], x[2]['topic']))
        selected = exact[0] if exact else (scored[0][2] if scored and scored[0][0] >= 0.5 else None)
        confidence = 1.0 if exact else (scored[0][0] if selected else 0.0)
        method = 'normalized exact' if exact else 'token overlap'
        if not exact and self.model:
            choice = self.model([{'role': 'system', 'content': 'Match the task to a known responsibility. Return JSON {"topic": string|null, "confidence": number}. Select null if unrelated. Known topics: ' + json.dumps([r['topic'] for r in responsibilities])}, {'role': 'user', 'content': phrasing}])
            selected = next((r for r in responsibilities if r['topic'] == choice.get('topic')), None)
            confidence = float(choice.get('confidence', 0))
            method = 'model-assisted match'
        if not selected or confidence < 0.5:
            return {'topic': None, 'owner': None, 'chain': [], 'confidence': confidence, 'method': method}
        people = {n['id']: n for n in pipeline['nodes']}
        chain = [people[r['person_email']] for r in sorted(pipeline['routing_chains'], key=lambda r: r['depth']) if r['topic'] == selected['topic']]
        return {'topic': selected['topic'], 'owner': people[selected['owner_email']], 'chain': chain,
                'confidence': confidence, 'source': selected['source'], 'method': method}

    def request(self, doc, person, question, *, previous=None, rationale=None, routing_only=False, choices=None, decision_id=None, followup=False):
        if not question or not question.strip():
            raise ValueError('Question must not be empty')
        previous_person = next((r['asked_person_email'] for r in reversed(doc['requests']) if r['status'] in {'answered', 'superseded'}), None)
        traversed = next((e['id'] for e in self.pipeline(doc['batch_id'])['edges'] if e['src'] == previous_person and e['dst'] == person['id']), None)
        req = {'request_id': uuid4().hex, 'run_id': doc['run_id'], 'asked_person_email': person['id'],
               'question': question.strip(), 'status': 'pending', 'answer': None, 'created_at': now_iso(),
               'answered_at': None, 'previous_request_id': previous, 'nudge_count': 0, 'routing_only': routing_only}
        if choices:
            req.update(choices=choices, decision_id=decision_id, followup=followup)
        doc['requests'].append(req)
        doc['status'] = 'waiting_on_human'
        doc['messages'].append({'role': 'assistant', 'content': f'Question to {person["name"]} ({person["id"]}): {question}'})
        self.event(doc, 'ask_human', f'Waiting for {person["name"]}: {question}', target=person['id'], edge=traversed,
                   result={'request_id': req['request_id'], 'question': question}, rationale=rationale)

    def _local_action(self, doc, pipeline):
        """Explicit offline adapter; actual requests/events, no pretend LLM or timed replies."""
        answered = [r for r in doc['requests'] if r['status'] == 'answered' and not r.get('routing_only')]
        if not answered:
            return {'action': 'ask', 'person_email': doc['route']['owner']['id'],
                    'question': f'For this task — {doc["task"]} — what is the affected scope, what has been confirmed, and what action or constraint should guide the response?',
                    'rationale': 'This employee owns the matching responsibility in the published data.'}
        last = answered[-1]
        answer = last['answer']
        people = pipeline['nodes']
        refer = re.search(r'\b(?:refer(?:\s+to)?|ask|contact|talk\s+to|check\s+with)\s+(.+)', answer, re.I)
        # Handle a clarification reply with only a name/email after an unresolved referral.
        previous_question = last['question'].lower()
        target_text = refer.group(1) if refer else answer if 'name or email' in previous_question else None
        if target_text:
            phrase = normalize(target_text)
            matches = [p for p in people if p['id'] in phrase or any(normalize(a) in phrase for a in [p['name'], p['title'], *p.get('aliases', [])])]
            if not matches:
                matches = [p for p in people if p['name'].split()[0].lower() in tokens(phrase)]
            if len(matches) == 1:
                return {'action': 'ask', 'person_email': matches[0]['id'],
                        'question': f'{last["asked_person_email"]} referred this task to you: {doc["task"]}. What specific clarification or recommended action can you provide?',
                        'rationale': f'Following the employee referral: {answer}'}
            return {'action': 'ask', 'person_email': last['asked_person_email'],
                    'question': 'That referral does not identify one employee in the directory. What is their name or email?',
                    'rationale': 'An ambiguous or absent employee must be clarified before routing.'}
        if re.search(r"\b(?:don.t know|not sure|no idea|unknown)\b", answer, re.I):
            visited = {r['asked_person_email'] for r in answered}
            manager = next((e['dst'] for e in pipeline['edges'] if e['src'] == last['asked_person_email'] and e['dst'] not in visited), None)
            return {'action': 'ask', 'person_email': manager or last['asked_person_email'],
                    'question': f'For {doc["task"]}, what confirmed information or named colleague can help us decide the next action?',
                    'rationale': 'The answer did not provide enough information; asking for a concrete next source.'}
        evidence = '\n'.join(f'- {r["asked_person_email"]}: {r["answer"]}' for r in answered)
        return {'action': 'conclude', 'answer': f'For {doc["task"]}, use the employee guidance below to determine the response.\n{evidence}\nThese are employee-reported facts; verify the operational outcome before closing the incident.',
                'rationale': 'The employee supplied actionable guidance. Offline mode summarizes it without inventing operational actions.'}

    def next_action(self, doc, pipeline):
        if doc.get('scenario'):
            from .wildlife import next_action
            return next_action(doc)
        if not self.model:
            return self._local_action(doc, pipeline)
        directory = [{'email': n['id'], 'name': n['name'], 'title': n['title'], 'team': n['team']} for n in pipeline['nodes']]
        system = ('You are an employee completing the user task through specific clarification questions. '
                  'Follow explicit employee referrals when they identify a unique directory person. '
                  'If a referral is ambiguous or absent (e.g. CEO is not listed), ask the referring employee for name/email. '
                  'Choose the next relevant employee using responsibilities, previous answers, and reporting relationships. '
                  'Never invent facts, people, answers or actions performed. Ask one question per turn. '
                  'Conclude with an actionable recommendation when sufficient information exists. '
                  'Return JSON only: {"action":"ask", "person_email":"...", "question":"...", "rationale":"brief stated reason"} '
                  'or {"action":"conclude", "answer":"...", "rationale":"..."}. '
                  'Directory: ' + json.dumps(directory) + '\nReporting: ' + json.dumps(pipeline['edges']) +
                  '\nResponsibilities: ' + json.dumps(pipeline['silver_responsibilities']) +
                  '\nInitial routing tool result: ' + json.dumps(doc['route']))
        return self.model([{'role': 'system', 'content': system}, *doc['messages']])

    def execute(self, run_id):
        doc = self.get(run_id)
        if doc['status'] not in {'running', 'resumed'} or doc.get('execution_error'):
            return
        try:
            with _mlflow_span(f'watcher segment {run_id}', get_settings(), {'run_id': run_id}):
                pipeline = self.pipeline(doc['batch_id'])
                if not doc['route']:
                    with _mlflow_span('who_should_i_ask', get_settings()):
                        doc['route'] = self.who_should_i_ask(doc['task'], pipeline, doc.get('scenario'))
                    route = doc['route']
                    self.event(doc, 'who_should_i_ask', f'Routing topic: {route["topic"] or "no safe match"}',
                               target=route['owner']['id'] if route['owner'] else None, result=route)
                    doc = self.commit(doc)
                    if not route['owner']:
                        self.event(doc, 'routing_unresolved', 'No responsibility matched; requester clarification required.', actor='system')
                        requester = next((p for p in pipeline['nodes'] if p['id'] == doc['requester']), None)
                        if not requester:
                            raise ValueError('No routing match. Start as an employee so the agent can request clarification.')
                        self.request(doc, requester, 'Which known responsibility or employee does this task concern? Please provide the topic or owner name/email.', routing_only=True)
                        self.commit(doc)
                        return
                # Incorporate routing clarification without fabricating an owner.
                if not doc['route']['owner']:
                    answers = [r for r in doc['requests'] if r['status'] == 'answered']
                    if answers:
                        doc['route'] = self.who_should_i_ask(answers[-1]['answer'], pipeline)
                        self.event(doc, 'who_should_i_ask', 'Re-evaluated routing using employee clarification.', result=doc['route'],
                                   target=doc['route']['owner']['id'] if doc['route']['owner'] else None)
                        if not doc['route']['owner']:
                            phrase = normalize(answers[-1]['answer'])
                            matches = [p for p in pipeline['nodes'] if p['id'] in phrase or normalize(p['name']) in phrase]
                            if len(matches) == 1:
                                doc['route'] = {'topic': 'employee clarification', 'owner': matches[0], 'chain': [matches[0]],
                                                'confidence': 1.0, 'method': 'explicit requester selection'}
                    if not doc['route']['owner']:
                        requester = next(p for p in pipeline['nodes'] if p['id'] == doc['requester'])
                        self.request(doc, requester, 'I still cannot identify an owner. Please provide one known employee name or email.', routing_only=True)
                        self.commit(doc)
                        return
                action = doc.get('next_action')
                if action is None:
                    action = self.next_action(doc, pipeline)
                    doc['next_action'] = action
                    self.event(doc, 'decide', action.get('rationale', 'Selected the next task action.'),
                               result=action, rationale=action.get('rationale'))
                    doc = self.commit(doc)
                doc['execution_error'] = None
                if action.get('action') == 'ask':
                    person = next((p for p in pipeline['nodes'] if p['id'] == normalize(action.get('person_email', ''))), None)
                    if not person:
                        raise ValueError('Agent selected an unknown employee; retry with a valid directory recipient.')
                    self.request(doc, person, action.get('question', ''), rationale=action.get('rationale'),
                                 choices=action.get('choices'), decision_id=action.get('decision_id'), followup=action.get('followup', False))
                elif action.get('action') == 'conclude' and any(r['status'] == 'answered' and not r.get('routing_only') for r in doc['requests']):
                    answer = str(action.get('answer', '')).strip()
                    if not answer:
                        raise ValueError('Agent returned an empty recommendation')
                    doc['status'], doc['answer'] = 'done', answer
                    doc['messages'].append({'role': 'assistant', 'content': answer})
                    self.event(doc, 'conclude', answer, rationale=action.get('rationale'), result={'answer': answer})
                else:
                    raise ValueError('Agent must request clarification before concluding')
                doc.pop('next_action', None)
                self.commit(doc)
        except Conflict:
            return  # Another transition (answer/cancel/reroute) already won.
        except Exception as exc:
            # Reload authoritative state: a projection failure must not overwrite a committed wait.
            current = self.get(run_id)
            if current['status'] in {'running', 'resumed'}:
                current['execution_error'] = f'{type(exc).__name__}: {exc}'
                self.event(current, 'execution_error', current['execution_error'], actor='system')
                try:
                    self.commit(current)
                except Conflict:
                    pass

    def pending(self, person=None):
        return [r for doc in self.runs() for r in doc['requests'] if r['status'] == 'pending' and (not person or r['asked_person_email'] == normalize(person))]

    def answer(self, request_id, answer, person, option_id=None, explanation=''):
        if not option_id and not answer.strip():
            raise ValueError('Answer must not be empty')
        with self.lock:
            doc = next((d for d in self.runs() if any(r['request_id'] == request_id for r in d['requests'])), None)
            if not doc:
                raise KeyError('Unknown request')
            request = next(r for r in doc['requests'] if r['request_id'] == request_id)
            if request['status'] != 'pending' or doc['status'] != 'waiting_on_human':
                raise Conflict('Request is no longer pending')
            if request['asked_person_email'] != normalize(person):
                raise ValueError('Switch Act as to the employee this request is addressed to')
            if request.get('choices'):
                from .wildlife import STAGES, classify
                stage = next(s for s in STAGES if s['id'] == request['decision_id'])
                result = classify(stage, option_id, explanation)
                answer = result['text']
                request.update(option_id=option_id, explanation=explanation.strip(), resolved=result['resolved'])
                if result['resolved']:
                    doc['scenario_decisions'][stage['id']] = {'answer': answer, 'person_email': person,
                                                             'request_id': request_id, 'option_id': option_id}
            elif not answer.strip():
                raise ValueError('Answer must not be empty')
            request.update(status='answered', answer=answer.strip(), answered_at=now_iso())
            doc['status'] = 'resumed'
            doc['messages'].append({'role': 'user', 'content': f'Answer from {person}: {answer.strip()}'})
            self.event(doc, 'human_answer', answer.strip(), actor=f'human:{person}', target=person,
                       result={'request_id': request_id})
            self.commit(doc)
        self.schedule(doc['run_id'])
        return self.get(doc['run_id'])

    def control(self, run_id, action, actor):
        with self.lock:
            doc = self.get(run_id)
            if action in {'retry', 'repair'}:
                if action == 'retry' and (doc['status'] not in {'running', 'resumed'} or not doc.get('execution_error')):
                    raise Conflict('Only a failed execution segment can be retried')
                if action == 'retry':
                    doc['execution_error'] = None
                    doc.pop('next_action', None)
                self.event(doc, action, 'Retrying execution from checkpoint.' if action == 'retry' else 'Repairing query tables from the durable checkpoint.', actor=actor)
                self.commit(doc)
            else:
                if doc['status'] != 'waiting_on_human':
                    raise Conflict('Controls require a waiting run')
                request = next(r for r in doc['requests'] if r['status'] == 'pending')
                if action == 'nudge':
                    request['nudge_count'] += 1
                    self.event(doc, 'nudge', 'Employee nudged in the demo inbox.', actor=actor, target=request['asked_person_email'])
                elif action == 'cancel':
                    request['status'] = 'cancelled'
                    doc['status'] = 'cancelled'
                    self.event(doc, 'cancel', 'Run cancelled by a human.', actor=actor, target=request['asked_person_email'])
                elif action == 'reroute':
                    pipeline = self.pipeline(doc['batch_id'])
                    edge = next((e for e in pipeline['edges'] if e['src'] == request['asked_person_email']), None)
                    if not edge:
                        raise ValueError('No validated manager exists for this employee')
                    manager = next(p for p in pipeline['nodes'] if p['id'] == edge['dst'])
                    request['status'] = 'superseded'
                    self.event(doc, 'reroute', f'Rerouted question to {manager["name"]}.', actor=actor,
                               target=manager['id'], edge=edge['id'])
                    self.request(doc, manager, request['question'], previous=request['request_id'], rationale='Explicit human reroute to validated manager.')
                else:
                    raise ValueError('Unknown control')
                return self.commit(doc)
        if self.get(run_id)['status'] in {'running', 'resumed'}:
            self.schedule(run_id)
        return self.get(run_id)


_watcher = None


def get_watcher():
    global _watcher
    if _watcher is None:
        if os.getenv('AFD_WORKFLOW', 'sentinel') == 'sentinel':
            from .sentinel import SentinelWatcher
            db = open_documents()
            if db.mode != 'delta':
                raise ValueError('Sentinel runtime requires AFD_DELTA=1 and a Unity Catalog SQL warehouse.')
            _watcher = SentinelWatcher(db)
        else:
            _watcher = Watcher(open_documents())
    return _watcher
