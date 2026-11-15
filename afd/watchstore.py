"""Durable Watcher documents with CAS updates and replayable Delta projections.

One run document is the atomic checkpoint, request history, and event outbox.
Physical query tables are idempotent projections, never the resume authority.
"""
from __future__ import annotations

import copy
import json
import os
import re
import threading
from pathlib import Path
from uuid import uuid4

from .audit import now_iso


class Conflict(ValueError):
    pass


class Documents:
    def __init__(self, directory: Path):
        self.directory = Path(directory) / 'watcher'
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.mode = 'local'

    def get(self, key):
        path = self.directory / f'{key}.json'
        with self.lock:
            return json.loads(path.read_text()) if path.exists() else None

    def list(self, prefix):
        with self.lock:
            return [json.loads(p.read_text()) for p in sorted(self.directory.glob(f'{prefix}*.json'))]

    def put(self, document, expected_revision=0):
        import fcntl
        doc = copy.deepcopy(document)
        with self.lock, (self.directory / '.write.lock').open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            previous = self.get(doc['id'])
            if (previous or {}).get('revision', 0) != expected_revision:
                raise Conflict('State changed; refresh and retry.')
            doc['revision'] = expected_revision + 1
            doc['updated_at'] = now_iso()
            path = self.directory / f'{doc["id"]}.json'
            temp = path.with_suffix(f'.{uuid4().hex}.tmp')
            with temp.open('w') as f:
                json.dump(doc, f)
                f.flush()
                os.fsync(f.fileno())
            temp.replace(path)
        return doc

    def project(self, doc):
        pass  # Local reads use authoritative documents directly.


# Field-level physical Delta projections; the complete record is retained too.
TABLES = {
    'bronze_org_chart': {'batch_id': 'STRING', 'source': 'STRING', 'source_row': 'INT', 'ingest_timestamp': 'TIMESTAMP',
                        'name': 'STRING', 'email': 'STRING', 'title': 'STRING', 'team': 'STRING', 'manager_email': 'STRING'},
    'bronze_responsibilities': {'batch_id': 'STRING', 'source': 'STRING', 'source_row': 'INT', 'ingest_timestamp': 'TIMESTAMP',
                              'topic': 'STRING', 'owner_email': 'STRING'},
    'bronze_person_context': {'batch_id': 'STRING', 'source': 'STRING', 'source_row': 'INT', 'ingest_timestamp': 'TIMESTAMP',
                            'person_email': 'STRING', 'note': 'STRING'},
    'person_context': {'batch_id': 'STRING', 'person_email': 'STRING', 'note': 'STRING', 'source': 'STRING', 'ingest_timestamp': 'TIMESTAMP'},
    'silver_people': {'batch_id': 'STRING', 'name': 'STRING', 'title': 'STRING', 'team': 'STRING'},
    'silver_responsibilities': {'batch_id': 'STRING', 'topic': 'STRING', 'owner_email': 'STRING'},
    'nodes': {'batch_id': 'STRING', 'name': 'STRING', 'title': 'STRING', 'team': 'STRING', 'type': 'STRING',
              'properties': 'STRING', 'source': 'STRING', 'confidence': 'DOUBLE', 'updated_at': 'TIMESTAMP'},
    'edges': {'batch_id': 'STRING', 'src': 'STRING', 'dst': 'STRING', 'type': 'STRING',
              'source': 'STRING', 'confidence': 'DOUBLE', 'updated_at': 'TIMESTAMP'},
    'quarantine': {'batch_id': 'STRING', 'reason': 'STRING', 'group_id': 'STRING', 'source': 'STRING'},
    'routing_chains': {'batch_id': 'STRING', 'topic': 'STRING', 'owner_email': 'STRING', 'person_email': 'STRING', 'depth': 'INT'},
    'pipeline_stats': {'batch_id': 'STRING', 'raw_records': 'INT', 'raw_people': 'INT', 'unique_people': 'INT',
                       'duplicates_merged': 'INT', 'violations_quarantined': 'INT', 'quarantined_count': 'INT'},
    'pending_requests': {'request_id': 'STRING', 'run_id': 'STRING', 'asked_person_email': 'STRING', 'question': 'STRING',
                         'status': 'STRING', 'answer': 'STRING', 'created_at': 'TIMESTAMP', 'answered_at': 'TIMESTAMP'},
    'events': {'run_id': 'STRING', 'step': 'INT', 'actor': 'STRING', 'action': 'STRING', 'target_node': 'STRING',
               'edge': 'STRING', 'result_summary': 'STRING', 'state': 'STRING', 'timestamp': 'TIMESTAMP'},
    'runs': {'run_id': 'STRING', 'status': 'STRING', 'task': 'STRING', 'batch_id': 'STRING', 'updated_at': 'TIMESTAMP'},
}


class DeltaDocuments:
    """Synchronous SQL persistence. Failures propagate; no local-only fallback.

    Run one App process/replica. CAS protects existing documents; initial
    creation also uses the single writer because UC keys are informational.
    """
    def __init__(self, warehouse_id, catalog, schema, client=None):
        from databricks.sdk import WorkspaceClient
        for value in (catalog, schema):
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', value):
                raise ValueError('catalog/schema must be SQL identifiers')
        self.client = client or WorkspaceClient()
        self.warehouse_id = warehouse_id
        self.fq = f'`{catalog}`.`{schema}`'
        self.lock = threading.RLock()
        self.mode = 'delta'

    def sql(self, statement, values=None):
        from databricks.sdk.service.sql import StatementParameterListItem
        parameters = [StatementParameterListItem(name=k, value=None if v is None else str(v)) for k, v in (values or {}).items()]
        r = self.client.statement_execution.execute_statement(statement=statement, warehouse_id=self.warehouse_id,
                                                              parameters=parameters, wait_timeout='50s')
        # These are SQL transport waits, never human-response/escalation waits.
        import time
        deadline = time.monotonic() + 180
        while r.status.state.value in {'PENDING', 'RUNNING'}:
            if time.monotonic() > deadline:
                raise RuntimeError(f'SQL execution still pending: {r.statement_id}; retry/recover before continuing')
            time.sleep(0.25)
            r = self.client.statement_execution.get_statement(r.statement_id)
        if r.status.state.value != 'SUCCEEDED':
            raise RuntimeError(f'Databricks SQL failed: {r.status.error}')
        if r.manifest and r.manifest.truncated:
            raise RuntimeError('SQL result truncated; refusing incomplete state')
        rows = list(r.result.data_array or []) if r.result else []
        chunk = r.result.next_chunk_index if r.result else None
        while chunk is not None:
            part = self.client.statement_execution.get_statement_result_chunk_n(r.statement_id, chunk)
            rows.extend(part.data_array or [])
            chunk = part.next_chunk_index
        return rows

    def bootstrap(self):
        self.sql(f'CREATE SCHEMA IF NOT EXISTS {self.fq} COMMENT \'Agent Watcher workflow and bronze silver gold data\'')
        self.sql(f'''CREATE TABLE IF NOT EXISTS {self.fq}.workflow_documents (
            id STRING NOT NULL COMMENT 'Stable checkpoint or pipeline document identifier',
            revision BIGINT COMMENT 'Compare-and-swap revision',
            token STRING COMMENT 'Writer operation identifier',
            record STRING COMMENT 'Authoritative checkpoint with request and event outbox',
            updated_at TIMESTAMP COMMENT 'UTC modification time',
            CONSTRAINT watcher_document_pk PRIMARY KEY (id) NOT ENFORCED
        ) USING DELTA COMMENT 'Atomic workflow authority; projections can be repaired from these records' ''')
        for table, columns in TABLES.items():
            fields = ', '.join(f'`{k}` {t}' + (' NOT NULL' if k == 'batch_id' else '') + f" COMMENT '{k.replace('_', ' ')}'" for k, t in columns.items())
            key_columns = 'batch_id, id' if 'batch_id' in columns else 'id'
            foreign_keys = (f', CONSTRAINT edges_src_fk FOREIGN KEY (batch_id, src) REFERENCES {self.fq}.nodes(batch_id, id) NOT ENFORCED, CONSTRAINT edges_dst_fk FOREIGN KEY (batch_id, dst) REFERENCES {self.fq}.nodes(batch_id, id) NOT ENFORCED' if table == 'edges' else '')
            self.sql(f'''CREATE TABLE IF NOT EXISTS {self.fq}.`{table}` (
                id STRING NOT NULL COMMENT 'Stable row id; person id is normalized email; batch is a separate key',
                {fields}, record STRING COMMENT 'Complete source record as JSON',
                CONSTRAINT {table}_pk PRIMARY KEY ({key_columns}) NOT ENFORCED {foreign_keys}
            ) USING DELTA COMMENT 'Agent Watcher {table}; replayable projection' ''')
        active = f"get_json_object((SELECT record FROM {self.fq}.workflow_documents WHERE id = 'active_pipeline'), '$.batch_id')"
        self.sql(f'CREATE OR REPLACE VIEW {self.fq}.nodes_current AS SELECT * FROM {self.fq}.nodes WHERE batch_id = {active}')
        self.sql(f'CREATE OR REPLACE VIEW {self.fq}.edges_current AS SELECT * FROM {self.fq}.edges WHERE batch_id = {active}')
        self.sql(f'''CREATE OR REPLACE VIEW {self.fq}.topic_routing
            COMMENT 'Filter by topic and ORDER BY depth: owner then successive managers'
            AS SELECT * FROM {self.fq}.routing_chains WHERE batch_id = {active}''')

    def get(self, key):
        rows = self.sql(f'SELECT record FROM {self.fq}.workflow_documents WHERE id = :key', {'key': key})
        return json.loads(rows[0][0]) if rows else None

    def list(self, prefix):
        rows = self.sql(f'SELECT record FROM {self.fq}.workflow_documents WHERE startswith(id, :prefix)', {'prefix': prefix})
        return [json.loads(row[0]) for row in rows]

    def put(self, document, expected_revision=0):
        doc = copy.deepcopy(document)
        doc.update(revision=expected_revision + 1, updated_at=now_iso())
        token = uuid4().hex
        with self.lock:
            self.sql(f'''MERGE INTO {self.fq}.workflow_documents t
                USING (SELECT :id AS id) s ON t.id = s.id
                WHEN MATCHED AND t.revision = CAST(:expected AS BIGINT) THEN UPDATE SET
                    revision = CAST(:revision AS BIGINT), token = :token, record = :record, updated_at = current_timestamp()
                WHEN NOT MATCHED AND CAST(:expected AS BIGINT) = 0 THEN INSERT
                    (id, revision, token, record, updated_at) VALUES
                    (:id, CAST(:revision AS BIGINT), :token, :record, current_timestamp())''',
                {'id': doc['id'], 'expected': expected_revision, 'revision': doc['revision'], 'token': token, 'record': json.dumps(doc)})
            rows = self.sql(f'SELECT token FROM {self.fq}.workflow_documents WHERE id = :id', {'id': doc['id']})
            if not rows or rows[0][0] != token:
                raise Conflict('State changed; refresh and retry.')
        return doc

    def _project_row(self, table, key, record, immutable=False):
        return self._project_rows(table, [(key, record)], immutable)

    def _project_rows(self, table, rows, immutable=False):
        if not rows:
            return
        columns = TABLES[table]
        names = ['id', *columns, 'record']
        payload = []
        for key, record in rows:
            item = {'id': key, 'record': json.dumps(record)}
            for col in columns:
                value = record.get(col)
                item[col] = json.dumps(value) if isinstance(value, (dict, list)) else value
            payload.append(item)
        # One SQL statement per table, rather than one warehouse roundtrip per row.
        schema = ','.join(f'{name}:{columns.get(name, "STRING")}' for name in names)
        update = '' if immutable else 'WHEN MATCHED THEN UPDATE SET ' + ', '.join(f't.`{k}` = s.`{k}`' for k in names if k != 'id')
        join = 't.id = s.id' + (' AND t.batch_id = s.batch_id' if 'batch_id' in columns else '')
        self.sql(f'''MERGE INTO {self.fq}.`{table}` t USING
            (SELECT inline(from_json(:rows, 'ARRAY<STRUCT<{schema}>>'))) s ON {join}
            {update}
            WHEN NOT MATCHED THEN INSERT ({', '.join(f'`{k}`' for k in names)})
            VALUES ({', '.join(f's.`{k}`' for k in names)})''', {'rows': json.dumps(payload)})

    def project(self, doc):
        with self.lock:
            # Always project latest revision to prevent a delayed writer undoing a later transition.
            doc = self.get(doc['id']) or doc
            if doc['id'].startswith('pipeline:'):
                batch = doc['batch_id']
                for kind, rows in doc['bronze'].items():
                    self._project_rows(f'bronze_{kind}', [(row['row_id'], row) for row in rows], immutable=True)
                for table in ('silver_people', 'silver_responsibilities', 'nodes', 'edges', 'quarantine', 'routing_chains', 'person_context'):
                    self._project_rows(table, [(row['id'], row) for row in doc.get(table, [])], immutable=True)
                self._project_row('pipeline_stats', batch, dict(doc['stats'], batch_id=batch), immutable=True)
            elif doc['id'].startswith('run:'):
                self._project_row('runs', doc['run_id'], doc)
                self._project_rows('pending_requests', [(r['request_id'], r) for r in doc['requests']])
                self._project_rows('events', [(e['event_id'], e) for e in doc['events']], immutable=True)

    def events(self, run_id):
        return [json.loads(row[0]) for row in self.sql(
            f'SELECT record FROM {self.fq}.events WHERE run_id = :run_id ORDER BY step', {'run_id': run_id})]

    def pipeline_rows(self, table, batch_id):
        if table not in TABLES or 'batch_id' not in TABLES[table]:
            raise ValueError('Invalid pipeline table')
        return [json.loads(row[0]) for row in self.sql(
            f'SELECT record FROM {self.fq}.`{table}` WHERE batch_id = :batch', {'batch': batch_id})]


def open_documents():
    if os.getenv('AFD_DELTA', '0').lower() in {'1', 'true'}:
        warehouse = os.getenv('AFD_WAREHOUSE_ID')
        if not warehouse:
            raise ValueError('AFD_WAREHOUSE_ID is required in Databricks mode')
        return DeltaDocuments(warehouse, os.getenv('AFD_CATALOG', 'workspace'),
                              os.getenv('AFD_WATCHER_SCHEMA', 'sentinel_gold'))
    return Documents(Path(os.getenv('AFD_DATA_DIR', 'data')))
