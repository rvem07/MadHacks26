"""Unity Catalog Delta persistence; existing CAS checkpoints and batched outbox projections."""
import copy
import json
import os
import re
import threading
from .audit import now_iso
from uuid import uuid4

class Conflict(ValueError):
    pass

META={'batch_id':'STRING','source_file':'STRING','source_row':'INT','ingested_at':'TIMESTAMP'}
PERSON={'batch_id':'STRING','type':'STRING','name':'STRING','title':'STRING','team':'STRING',
        'expertise':'STRING','context_notes':'STRING','needs_review':'BOOLEAN','review_reason':'STRING',
        'source':'STRING','confidence':'DOUBLE','updated_at':'TIMESTAMP'}
EDGE={'batch_id':'STRING','src':'STRING','dst':'STRING','type':'STRING','context':'STRING',
      'status':'STRING','source':'STRING','confidence':'DOUBLE','updated_at':'TIMESTAMP'}
TABLES={
 'raw_files':{'batch_id':'STRING','filename':'STRING','content':'STRING','sha256':'STRING','source_file':'STRING','ingested_at':'TIMESTAMP'},
 'bronze_org_chart':{**META,**{k:'STRING' for k in ['name','email','title','team','manager_email']}},
 'bronze_collaborations':{**META,**{k:'STRING' for k in ['person_a','person_b','context']}},
 'bronze_person_context':{**META,**{k:'STRING' for k in ['email','expertise','context_notes']}},
 'silver_people':PERSON,'silver_relationships':EDGE,'nodes':PERSON,'edges':EDGE,
 'quarantine':{'batch_id':'STRING','record_type':'STRING','record_key':'STRING','reason':'STRING','candidate_values':'STRING','created_at':'TIMESTAMP'},
 'pipeline_stats':{'batch_id':'STRING',**{k:'INT' for k in ['raw_people_rows','unique_people','fuzzy_duplicates_merged','exact_duplicates_collapsed','relationships_quarantined','raw_collab_rows','valid_collab_edges']},'run_at':'TIMESTAMP'},
 'runs':{'run_id':'STRING','status':'STRING','task':'STRING','batch_id':'STRING','created_at':'TIMESTAMP'},
 'events':{'run_id':'STRING','seq':'INT','ts':'TIMESTAMP','type':'STRING','actor':'STRING','target_person':'STRING','payload':'STRING','duration_ms':'BIGINT'},
 'run_context':{'run_id':'STRING','person_email':'STRING','question':'STRING','answer':'STRING','cached':'BOOLEAN','created_at':'TIMESTAMP'},
 'persona_cache':{'person_email':'STRING','question_hash':'STRING','answer':'STRING','created_at':'TIMESTAMP'},
 'research':{'run_id':'STRING','query':'STRING','url':'STRING','title':'STRING','key_points':'STRING','created_at':'TIMESTAMP'},
 'artifacts':{'run_id':'STRING','path':'STRING','content':'STRING','created_at':'TIMESTAMP'},
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
        self._projected = {}

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
        self.sql(f"CREATE SCHEMA IF NOT EXISTS {self.fq} COMMENT 'Fernwood Agent Watcher: pipeline and event-driven runs'")
        self.sql(f"""CREATE TABLE IF NOT EXISTS {self.fq}.workflow_documents (
            id STRING NOT NULL COMMENT 'Checkpoint identifier', revision BIGINT COMMENT 'CAS revision',
            token STRING COMMENT 'Writer operation ID', record STRING COMMENT 'Checkpoint and durable event outbox',
            updated_at TIMESTAMP COMMENT 'Last checkpoint write',
            CONSTRAINT document_pk PRIMARY KEY(id) NOT ENFORCED
        ) USING DELTA COMMENT 'Recoverable single-writer Fernwood checkpoints'""")
        for table,columns in TABLES.items():
            fields=', '.join(f"`{k}` {t}"+(' NOT NULL' if k=='batch_id' else '')+f" COMMENT '{k.replace('_',' ')}'" for k,t in columns.items())
            keys='batch_id,id' if 'batch_id' in columns else 'id'
            self.sql(f"""CREATE TABLE IF NOT EXISTS {self.fq}.`{table}` (
                id STRING NOT NULL COMMENT 'Stable identity within batch or run', {fields},
                record STRING COMMENT 'Full record and provenance JSON',
                CONSTRAINT {table}_pk PRIMARY KEY({keys}) NOT ENFORCED
            ) USING DELTA COMMENT 'Fernwood {table}; idempotent durable projection'""")
        for table in ['nodes','edges','pipeline_stats']:
            self.sql(f"""CREATE OR REPLACE VIEW {self.fq}.{table}_current AS SELECT * FROM {self.fq}.{table}
                WHERE batch_id=get_json_object((SELECT record FROM {self.fq}.workflow_documents WHERE id='active_pipeline'),'$.batch_id')""")

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

    def project(self,doc):
        with self.lock:
            doc=self.get(doc['id']) or doc
            if doc['id'].startswith('pipeline:'):
                for kind,rows in doc['bronze'].items():
                    self._project_rows('bronze_'+kind,[(r['id'],r) for r in rows],immutable=True)
                for table in ['raw_files','silver_people','silver_relationships','nodes','edges','quarantine']:
                    self._project_rows(table,[(r['id'],r) for r in doc[table]],immutable=True)
                self._project_row('pipeline_stats',doc['batch_id'],dict(doc['stats'],batch_id=doc['batch_id']),immutable=True)
            elif doc['id'].startswith('run:'):
                marker=self._projected.setdefault(doc['id'],{})
                if marker.get('status')!=doc['status']:
                    self._project_row('runs',doc['run_id'],{k:doc[k] for k in ['run_id','status','task','batch_id','created_at']})
                    marker['status']=doc['status']
                for table in ['run_context','research','artifacts','events']:
                    rows=doc.get(table,[])
                    self._project_rows(table,[(r['id'],r) for r in rows[marker.get(table,0):]],immutable=True)
                    marker[table]=len(rows)
            elif doc['id'].startswith('cache:'):
                self._project_row('persona_cache',doc['id'],doc)

    def events(self,run_id,after=0):
        return [json.loads(r[0]) for r in self.sql(f'SELECT record FROM {self.fq}.events WHERE run_id=:run AND seq>CAST(:after AS INT) ORDER BY seq',{'run':run_id,'after':after})]

    def pipeline_rows(self,table,batch_id):
        if table not in TABLES or 'batch_id' not in TABLES[table]: raise ValueError('Unknown data table')
        return [json.loads(r[0]) for r in self.sql(f'SELECT record FROM {self.fq}.`{table}` WHERE batch_id=:batch',{'batch':batch_id})]

    def graph(self,batch=None):
        active=self.get('active_pipeline')
        if not active: raise ValueError('Run the Databricks pipeline first')
        batch=batch or active['batch_id']
        return {'batch_id':batch,'nodes':self.pipeline_rows('nodes',batch),
                'edges':self.pipeline_rows('edges',batch),'stats':self.pipeline_rows('pipeline_stats',batch)[0]}


def open_documents():
    warehouse=os.getenv('AFD_WAREHOUSE_ID')
    if not warehouse: raise ValueError('AFD_WAREHOUSE_ID is required; runtime has no local fallback')
    return DeltaDocuments(warehouse,os.getenv('AFD_CATALOG','workspace'),os.getenv('AFD_WATCHER_SCHEMA','fernwood'))
