import copy
import threading
import pytest
from afd.pipeline import build_pipeline
from afd.watchstore import Conflict

class MemoryDocuments:
    """Explicit test double only; no production local persistence backend."""
    mode='test';fq='test'
    def __init__(self):
        self.rows={};self.lock=threading.RLock();self.graph_data=build_pipeline('data')
    def get(self,key): return copy.deepcopy(self.rows.get(key))
    def list(self,prefix): return [copy.deepcopy(v) for k,v in self.rows.items() if k.startswith(prefix)]
    def put(self,row,expected_revision=0):
        if self.rows.get(row['id'],{}).get('revision',0)!=expected_revision: raise Conflict('revision')
        row=copy.deepcopy(row);row['revision']=expected_revision+1;self.rows[row['id']]=row;return copy.deepcopy(row)
    def project(self,row): pass
    def graph(self,batch=None): return copy.deepcopy(self.graph_data)
    def events(self,rid,after=0): return [e for e in self.get('run:'+rid)['events'] if e['seq']>after]

@pytest.fixture
def db(): return MemoryDocuments()
