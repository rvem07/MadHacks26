"""Versioned guided demo: requirements for an endangered-wildlife protection app.

Questions are deterministic; every answer is supplied by the demonstrator and
persisted through Watcher's normal human-request/checkpoint workflow.
"""
import re

SCENARIO = 'wildlife_protection_v1'
DEFAULT_PROMPT = ('Build a secure application for our endangered-animal startup. '
                  'Help conservation teams coordinate protection without exposing animal locations to hunters.')
STAGES = [
    {'id': 'users', 'person': 'maria.alvarez@badgernet.com', 'name': 'Maria Alvarez',
     'label': 'Primary users', 'question': 'Who should we build the first version for?',
     'followup': 'Which specific people will use the first version? Choose a group or name the roles in Other.',
     'choices': [('rangers', 'Field rangers'), ('researchers', 'Rangers and conservation researchers'),
                 ('partners', 'Rangers and approved rescue partners')],
     'recognize': r'\b(rangers?|researchers?|veterinarians?|vets?|conservationists?|rescuers?|wardens?|scientists?|staff|volunteers?|patrols?)\b',
     'reason': 'Maria oversees operations and can define the people and scope for the pilot.'},
    {'id': 'sightings', 'person': 'priya.sharma@badgernet.com', 'name': 'Priya Sharma',
     'label': 'Sighting collection', 'question': 'How should the app collect wildlife sightings?',
     'followup': 'What will supply the sightings: ranger reports, sensors, or both? Describe a concrete source in Other.',
     'choices': [('mobile', 'Ranger reports, with offline capture'), ('sensors', 'Connected camera and sensor reports'),
                 ('combined', 'Both ranger reports and sensor data')],
     'recognize': r'\b(rangers?|sensors?|cameras?|gps|trackers?|tracking|mobile|offline|uploads?|forms?)\b',
     'reason': 'Priya can clarify how field information reaches the application.'},
    {'id': 'location_access', 'person': 'rachel.kim@badgernet.com', 'name': 'Rachel Kim',
     'label': 'Location access', 'question': 'Who should see precise animal locations?',
     'followup': 'Which named role should have precise-location access? Choose a group or specify the role in Other.',
     'choices': [('assigned', 'Only rangers assigned to that reserve'), ('verified', 'All verified conservation staff'),
                 ('approved', 'Rangers and approved researchers')],
     'recognize': r'\b(rangers?|researchers?|veterinarians?|wardens?|staff|admins?|administrators?|nobody|public|everyone|rescuers?|volunteers?)\b',
     'reason': 'Rachel is the security lead; location access is central to protecting animals from hunters.'},
    {'id': 'alerts', 'person': 'lena.ortiz@badgernet.com', 'name': 'Lena Ortiz',
     'label': 'Field alerts', 'question': 'How should field teams receive and acknowledge urgent alerts?',
     'followup': 'Which alert channel should the team use? Pick an option or name a channel and response in Other.',
     'choices': [('push', 'App notification with an Acknowledge button'), ('sms', 'SMS alert with a reply to confirm'),
                 ('dispatch', 'Dispatcher contacts the team by radio and records confirmation')],
     'recognize': r'\b(sms|text|push|notification|notifications|radio|dispatcher|dispatch|phone|call|calls|email|messaging|signal|whatsapp)\b',
     'reason': 'Lena leads field services and can define a practical alert-and-response workflow.'},
    {'id': 'pilot', 'person': 'tom.becker@badgernet.com', 'name': 'Tom Becker',
     'label': 'Pilot rollout', 'question': 'How should we pilot the application before a wider launch?',
     'followup': 'What is the first pilot group or location? Choose a bounded pilot or describe one in Other.',
     'choices': [('reserve', 'One reserve with a small ranger team'), ('internal', 'Internal staff using fictional animal locations'),
                 ('partners', 'Two partner reserves with trained coordinators')],
     'recognize': r'\b(reserves?|parks?|sanctuar(?:y|ies)|internal|fictional|synthetic|test|testing|rangers?|staff|teams?)\b',
     'reason': 'Tom oversees operations readiness and can define a controlled rollout.'},
]


def choices(stage):
    return [{'id': key, 'label': label} for key, label in stage['choices']] + [{'id': 'other', 'label': 'Other'}]


def classify(stage, option_id, explanation):
    if option_id not in {c['id'] for c in choices(stage)}:
        raise ValueError('Select one of the available answers')
    if option_id != 'other':
        return dict(resolved=True, text=dict(stage['choices'])[option_id])
    text = explanation.strip()
    if not text:
        raise ValueError('Explain your answer when choosing Other')
    uncertain = re.search(r"\b(not sure|don.t know|unsure|undecided|whatever|no idea|maybe|idk|tbd|any (?:one|thing)|anyone|anything)\b", text, re.I)
    # Conservatively require a concrete decision for this stage, not length alone.
    concrete = re.search(stage['recognize'], text, re.I)
    return {'resolved': bool(concrete and not uncertain), 'text': text}


def next_action(doc):
    decisions = doc.get('scenario_decisions', {})
    stage = next((s for s in STAGES if s['id'] not in decisions), None)
    if stage:
        earlier = [r for r in doc['requests'] if r.get('decision_id') == stage['id'] and r['status'] == 'answered']
        return {'action': 'ask', 'person_email': stage['person'],
                'question': stage['followup'] if earlier else stage['question'], 'choices': choices(stage),
                'decision_id': stage['id'], 'followup': bool(earlier), 'rationale': stage['reason']}
    lines = ['Application plan ready', '', f'Task: {doc["task"]}', '',
             *[f'{s["label"]}: {decisions[s["id"]]["answer"]}' for s in STAGES], '',
             'Security foundation: verified accounts, role-based permissions, encrypted data, '
             'and an audit trail for access to animal locations. Public-facing information uses approximate areas.',
             'Build sequence: accounts and permissions → sightings and restricted map → alerts and acknowledgement → pilot review.']
    access = decisions['location_access']['answer'].lower()
    if re.search(r'\b(public|everyone)\b', access):
        lines.append('Open decision: Rachel must review the requested broad location access before implementation; '
                     'precise locations could expose animals to hunters.')
    return {'action': 'conclude', 'answer': '\n'.join(lines),
            'rationale': 'The five employee decisions have been clarified and recorded.'}
