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
    for kind, fields in [('org_chart', {'name', 'email', 'title', 'team', 'manager_email'}),
                         ('responsibilities', {'topic', 'owner_email'})]:
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
        topic_rows[normalize(raw['topic'])].append(raw)
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
    return {'id': f'pipeline:{batch}', 'batch_id': batch, 'created_at': now,
            'bronze': bronze, 'silver_people': people, 'silver_responsibilities': responsibilities,
            'nodes': nodes, 'edges': edges, 'quarantine': quarantine, 'routing_chains': routes, 'stats': stats,
            'source_digest': hashlib.sha256(json.dumps(bronze, sort_keys=True).encode()).hexdigest()}


def publish_pipeline(documents, directory=SAMPLE):
    """Project a complete immutable batch, then atomically activate it."""
    pipeline = documents.put(build_pipeline(directory))
    documents.project(pipeline)
    active = documents.get('active_pipeline')
    documents.put({'id': 'active_pipeline', 'batch_id': pipeline['batch_id']}, (active or {}).get('revision', 0))
    return pipeline
