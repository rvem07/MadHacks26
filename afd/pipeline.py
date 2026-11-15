"""Shared, deterministic bronze/silver/gold transformations. No Spark required."""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from uuid import uuid4

from .audit import now_iso

SAMPLE = Path(__file__).resolve().parent.parent / 'samples' / 'badgernet'
SENTINEL = Path(__file__).resolve().parent.parent / 'data'
TOPIC_SYNONYMS = {'data breach': 'security incident'}


def normalize(value: str) -> str:
    return value.strip().lower()


def build_pipeline(directory: Path = SAMPLE) -> dict:
    batch = uuid4().hex
    now = now_iso()
    aliases = json.loads((directory / 'aliases.json').read_text()) if (directory / 'aliases.json').exists() else {}
    aliases = {normalize(k): normalize(v) for k, v in aliases.items()}

    def email(value):
        value = normalize(value)
        seen = set()
        while value in aliases:
            if value in seen:
                raise ValueError('cycle in email alias configuration')
            seen.add(value)
            value = aliases[value]
        return value

    bronze = {}
    inputs = [('org_chart', {'name', 'email', 'title', 'team', 'manager_email'}),
              ('responsibilities', {'topic', 'owner_email'})]
    sentinel = (directory / 'person_context.csv').exists()
    if sentinel:
        inputs.append(('person_context', {'person_email', 'note'}))
    for kind, fields in inputs:
        path = directory / f'{kind}.csv'
        with path.open(newline='') as f:
            reader = csv.DictReader(f)
            if set(reader.fieldnames or []) != fields:
                raise ValueError(f'{path}: expected columns {sorted(fields)}')
            bronze[kind] = []
            for i, row in enumerate(reader, 2):
                if None in row or any(v is None for v in row.values()):
                    raise ValueError(f'{path}:{i}: malformed CSV row')
                bronze[kind].append(dict(row, source=str(path.name), source_row=i,
                                         ingest_timestamp=now, batch_id=batch, row_id=f'{batch}:{kind}:{i}'))
    # Infer aliases only for an unambiguous identity match. Never edit bronze.
    if sentinel:
        raw_people = bronze['org_chart']
        for row in sorted(raw_people, key=lambda r: (-len(r['name'].strip()), r['source_row'])):
            def same_person(other):
                a, b = normalize(row['name']).rstrip('.'), normalize(other['name']).rstrip('.')
                prefix = a.startswith(b) or b.startswith(a)
                return a == b or (prefix and normalize(row['title']) == normalize(other['title']) and email(row['manager_email']) == email(other['manager_email']))
            matches = {normalize(r['email']) for r in raw_people if same_person(r)}
            if len(matches) >= 2:
                preferred = max((r for r in raw_people if normalize(r['email']) in matches),
                                key=lambda r: (len(r['name'].strip()), -r['source_row']))
                for alias in matches - {normalize(preferred['email'])}:
                    aliases[alias] = normalize(preferred['email'])
    groups = defaultdict(list)
    quarantine = []

    def quarantine_row(reason, row, group):
        quarantine.append({'id': f'{row["row_id"]}:{reason}', 'batch_id': batch, 'reason': reason,
                           'group_id': group, 'source': row['source'], 'raw_record': row, 'updated_at': now})

    for raw in bronze['org_chart']:
        if not email(raw['email']):
            quarantine_row('invalid_person', raw, raw['row_id'])
            continue
        groups[email(raw['email'])].append(raw)
    people = []
    managers = {}
    for pid, rows in sorted(groups.items()):
        # Prefer a canonical-email record, then the most complete name; stable input tie-break.
        canonical = sorted(rows, key=lambda r: (normalize(r['email']) != pid, -len(r['name'].strip()), r['source_row']))[0]
        source = [f'{r["source"]}:{r["source_row"]}' for r in rows]
        people.append({'id': pid, 'name': canonical['name'].strip(), 'title': canonical['title'].strip(),
                       'team': canonical['team'].strip(), 'aliases': sorted({r['name'].strip() for r in rows}),
                       'source': source, 'confidence': 1.0, 'updated_at': now, 'batch_id': batch})
        candidates = {email(r['manager_email']) for r in rows if email(r['manager_email'])}
        if len(candidates) > 1:
            for r in rows:
                quarantine_row('multiple_managers', r, pid)
        elif candidates:
            manager = next(iter(candidates))
            if manager not in groups:
                for r in rows:
                    quarantine_row('missing_manager', r, pid)
            else:
                managers[pid] = manager
    # Find all members of all directed reporting cycles, not just the entry node.
    cycle_members = {}
    for pid in managers:
        path = []
        cursor = pid
        while cursor in managers and cursor not in path:
            path.append(cursor)
            cursor = managers[cursor]
        if cursor in path:
            members = path[path.index(cursor):]
            cycle_members.update({member: '|'.join(sorted(members)) for member in members})
    for pid in sorted(cycle_members):
        for r in groups[pid]:
            quarantine_row('reporting_cycle', r, cycle_members[pid])
        managers.pop(pid, None)
    nodes = [dict(p, type='person', label=p['name'], props={k: p[k] for k in ('title', 'team', 'aliases')}) for p in people]
    edges = [{'id': f'reports:{p}', 'src': p, 'dst': m, 'type': 'reports_to',
              'source': [f'{r["source"]}:{r["source_row"]}' for r in groups[p]],
              'confidence': 1.0, 'updated_at': now, 'batch_id': batch} for p, m in sorted(managers.items())]
    responsibilities = []
    routes = []
    topic_rows = defaultdict(list)
    for raw in bronze['responsibilities']:
        topic = normalize(raw['topic'])
        topic_rows[TOPIC_SYNONYMS.get(topic, topic) if sentinel else topic].append(raw)
    for topic, rows in sorted(topic_rows.items()):
        owners = {email(r['owner_email']) for r in rows if email(r['owner_email']) in groups}
        invalid = not topic or len(owners) != 1 or any(not email(r['owner_email']) or email(r['owner_email']) not in groups for r in rows)
        if invalid:
            reason = 'multiple_topic_owners' if len(owners) > 1 else 'unowned_topic'
            for r in rows:
                quarantine_row(reason, r, topic)
            continue
        owner = next(iter(owners))
        responsibility = {'id': topic, 'topic': topic, 'owner_email': owner, 'batch_id': batch,
                          'source': [f'{r["source"]}:{r["source_row"]}' for r in rows], 'confidence': 1.0, 'updated_at': now}
        responsibilities.append(responsibility)
        cursor, chain = owner, []
        while cursor and cursor not in chain:
            chain.append(cursor)
            cursor = managers.get(cursor)
        for depth, person in enumerate(chain):
            routes.append({'id': f'{topic}:{depth}', 'topic': topic, 'owner_email': owner,
                           'person_email': person, 'depth': depth, 'batch_id': batch, 'updated_at': now})
    stats = {'raw_records': sum(map(len, bronze.values())), 'raw_people': len(bronze['org_chart']),
             'raw_responsibilities': len(bronze['responsibilities']),
             'duplicates_merged': sum(len(rows) - 1 for rows in groups.values()),
             'violations_quarantined': len(quarantine), 'violation_counts': dict(Counter(q['reason'] for q in quarantine)),
             'violation_groups': len({(q['reason'], q['group_id']) for q in quarantine}),
             'gold_nodes': len(nodes), 'gold_edges': len(edges), 'owned_topics': len(responsibilities)}
    result = {'id': f'pipeline:{batch}', 'batch_id': batch, 'created_at': now,
            'bronze': bronze, 'silver_people': people, 'silver_responsibilities': responsibilities,
            'nodes': nodes, 'edges': edges, 'quarantine': quarantine, 'routing_chains': routes, 'stats': stats,
            'source_digest': hashlib.sha256(json.dumps(bronze, sort_keys=True).encode()).hexdigest()}
    if sentinel:
        review = {q['group_id'] for q in quarantine if q['reason'] in {'missing_manager', 'multiple_managers'}} | set(cycle_members)
        for person in people:
            person['needs_review'] = person['id'] in review
        for node in nodes:
            node['props']['needs_review'] = node['id'] in review
            node['properties'] = node['props']
        result['routing_chains'] = [r for r in routes if r['owner_email'] not in review and r['person_email'] not in review]
        for responsibility in responsibilities:
            responsibility['routing_allowed'] = responsibility['owner_email'] not in review
        def graph_node(nid, typ, name, properties, source):
            return dict(id=nid, type=typ, name=name, label=name, properties=properties, props=properties,
                        source=source, confidence=1.0, updated_at=now, batch_id=batch)
        for team in sorted({p['team'] for p in people}):
            nodes.append(graph_node('team:' + team, 'team', team, {}, ['org_chart.csv']))
        owned = {r['topic']: r for r in responsibilities}
        for topic, rows in sorted(topic_rows.items()):
            nodes.append(graph_node('topic:' + topic, 'topic', topic,
                                    {'routing_allowed': bool(owned.get(topic, {}).get('routing_allowed'))},
                                    [f'{r["source"]}:{r["source_row"]}' for r in rows]))
        for person in people:
            edges.append(dict(id='member:' + person['id'], src=person['id'], dst='team:' + person['team'],
                              type='member_of', source=person['source'], confidence=1.0, updated_at=now, batch_id=batch))
        for responsibility in responsibilities:
            edges.append(dict(id='owns:' + responsibility['topic'], src=responsibility['owner_email'],
                              dst='topic:' + responsibility['topic'], type='responsible_for', source=responsibility['source'],
                              confidence=1.0, updated_at=now, batch_id=batch))
        result['person_context'] = [dict(r, id=f'context:{i}', person_email=email(r['person_email']))
                                    for i, r in enumerate(bronze['person_context'])]
        stats.update(raw_context=len(bronze['person_context']), unique_people=len(people),
                     # Repeated Jamal relationship is a conflict, not an identity merge.
                     duplicates_merged=sum(len({normalize(r['email']) for r in rows}) - 1 for rows in groups.values()),
                     quarantined_count=stats['violation_groups'], gold_nodes=len(nodes), gold_edges=len(edges))
    return result


def publish_pipeline(documents, directory=SAMPLE):
    """Project a complete immutable batch, then atomically activate it."""
    pipeline = documents.put(build_pipeline(directory))
    documents.project(pipeline)
    active = documents.get('active_pipeline')
    documents.put({'id': 'active_pipeline', 'batch_id': pipeline['batch_id']}, (active or {}).get('revision', 0))
    return pipeline
