"""Deterministic transforms; bronze is preserved, gold activation is atomic."""
from __future__ import annotations
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from uuid import uuid4
from .audit import now_iso

INPUTS = {'org_chart': ['name','email','title','team','manager_email'],
          'collaborations': ['person_a','person_b','context'],
          'person_context': ['email','expertise','context_notes']}


def normalize(value):
    return value.strip().lower()


def identity_match(a, b):
    an, bn = normalize(a['name']).split(), normalize(b['name']).split()
    title = lambda x: normalize(x).replace('sr.', 'senior')
    return (an[-1] == bn[-1] and (an[0].startswith(bn[0]) or bn[0].startswith(an[0]))
            and normalize(a['team']) == normalize(b['team']) and title(a['title']) == title(b['title'])
            and normalize(a['manager_email']) == normalize(b['manager_email']))


def build_pipeline(directory, source_root=None):
    batch, now = uuid4().hex, now_iso()
    bronze, raw_files = {}, []
    for kind, fields in INPUTS.items():
        path = Path(directory) / f'{kind}.csv'
        raw = path.read_text()
        source = f'{source_root or directory}/{kind}.csv'
        raw_files.append({'id':kind, 'batch_id':batch,'filename':path.name,'content':raw,
                          'sha256':hashlib.sha256(raw.encode()).hexdigest(),'source_file':source,'ingested_at':now})
        with path.open(newline='') as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != fields:
                raise ValueError(f'{kind}: unexpected CSV columns')
            bronze[kind] = []
            for i,row in enumerate(reader,2):
                if None in row or any(v is None for v in row.values()):
                    raise ValueError(f'{kind}:{i}: malformed CSV')
                bronze[kind].append(dict(row,id=f'{kind}:{i}',batch_id=batch,source_file=source,source_row=i,ingested_at=now))
    raw = bronze['org_chart']
    aliases = {normalize(r['email']):normalize(r['email']) for r in raw}
    for row in sorted(raw,key=lambda r:(-len(r['name']),r['source_row'])):
        canonical = aliases[normalize(row['email'])]
        for other in raw:
            if identity_match(row,other): aliases[normalize(other['email'])] = canonical
    groups = defaultdict(list)
    for row in raw: groups[aliases[normalize(row['email'])]].append(row)
    # Context emails are intentionally used as-is, as specified by the source contract.
    context = {r['email']:r for r in bronze['person_context']}
    if set(context) != set(groups): raise ValueError('Context must cover exactly the canonical directory')
    nodes, edges, quarantine = [], [], []
    def edge(src,dst,typ,ctx,status,source,key):
        return dict(id=key,batch_id=batch,src=src,dst=dst,type=typ,context=ctx,status=status,
                    source=source,confidence=1.0 if status=='valid' else 0.0,updated_at=now)
    for pid, rows in sorted(groups.items()):
        person = min(rows,key=lambda r:(normalize(r['email'])!=pid,-len(r['name']),r['source_row']))
        managers = sorted({aliases.get(normalize(r['manager_email']),normalize(r['manager_email'])) for r in rows if r['manager_email'].strip()})
        reason = 'multiple_managers' if len(managers)>1 else 'manager_not_in_directory' if managers and managers[0] not in groups else ''
        source = [f"{r['source_file']}:{r['source_row']}" for r in rows]
        node = dict(id=pid,batch_id=batch,type='person',name=person['name'].strip(),title=person['title'].strip(),
                    team=person['team'].strip(),expertise=context[pid]['expertise'],context_notes=context[pid]['context_notes'],
                    needs_review=bool(reason),review_reason=reason,source=source,confidence=1.0,updated_at=now)
        nodes.append(node)
        if reason:
            quarantine.append(dict(id=pid,batch_id=batch,record_type='reports_to',record_key=pid,
                reason=reason,candidate_values=managers,created_at=now))
        for m in managers:
            edges.append(edge(pid,m,'reports_to',reason,'quarantined' if reason else 'valid',source,f'reports:{pid}:{m}'))
    for row in bronze['collaborations']:
        a,b = [aliases.get(normalize(row[k]),normalize(row[k])) for k in ('person_a','person_b')]
        valid = a in groups and b in groups and a != b
        if not valid:
            quarantine.append(dict(id=row['id'],batch_id=batch,record_type='works_with',record_key=row['id'],
                reason='invalid_collaboration',candidate_values=[a,b],created_at=now))
        edges.append(edge(a,b,'works_with',row['context'],'valid' if valid else 'quarantined',
                          [f"{row['source_file']}:{row['source_row']}"],row['id']))
    stats = dict(raw_people_rows=len(raw),unique_people=len(nodes),
        fuzzy_duplicates_merged=sum(k!=v for k,v in aliases.items()),
        exact_duplicates_collapsed=len(raw)-len({normalize(r['email']) for r in raw}),
        relationships_quarantined=len(quarantine),raw_collab_rows=len(bronze['collaborations']),
        valid_collab_edges=sum(e['type']=='works_with' and e['status']=='valid' for e in edges),run_at=now)
    return dict(id=f'pipeline:{batch}',batch_id=batch,created_at=now,bronze=bronze,raw_files=raw_files,
                silver_people=nodes,silver_relationships=edges,nodes=nodes,edges=edges,quarantine=quarantine,stats=stats,
                aliases=aliases)


def publish_pipeline(documents,directory,source_root=None):
    pipeline=documents.put(build_pipeline(directory,source_root))
    documents.project(pipeline)
    active=documents.get('active_pipeline')
    documents.put({'id':'active_pipeline','batch_id':pipeline['batch_id']},(active or {}).get('revision',0))
    return pipeline
