"""Durable CSV ingestion jobs; activate a batch only after every projection succeeds."""
import threading
from uuid import uuid4
from .audit import now_iso
from .pipeline import build_uploaded_pipeline,publish_prepared_pipeline
from .security import reject_secrets


class Ingestor:
    def __init__(self,db,background=True):
        self.db=db;self.background=background
        self.lock=threading.RLock();self.publish_lock=threading.Lock();self.workers={}

    def start(self,text,filename):
        reject_secrets(text)
        filename=filename.replace('\\','/').split('/')[-1]
        if not filename.lower().endswith('.csv'):raise ValueError('Select a .csv file')
        pipeline=build_uploaded_pipeline(text,filename)
        job_id=uuid4().hex
        doc=self.db.put({'id':'ingestion:'+job_id,'job_id':job_id,'status':'running','filename':filename,
            'created_at':now_iso(),'pipeline':pipeline,'message':'Building ontology from CSV…'})
        self.schedule(job_id)
        return self.summary(doc)

    def get(self,job_id):
        doc=self.db.get('ingestion:'+job_id)
        if not doc:raise KeyError('Unknown ingestion')
        return doc

    @staticmethod
    def summary(doc):
        return {k:doc[k] for k in ['job_id','status','filename','message','batch_id','stats'] if k in doc}

    def schedule(self,job_id):
        if not self.background:return
        with self.lock:
            if job_id in self.workers and self.workers[job_id].is_alive():return
            worker=threading.Thread(target=self.execute,args=(job_id,),daemon=True)
            self.workers[job_id]=worker;worker.start()

    def execute(self,job_id):
        with self.publish_lock:
            doc=self.get(job_id)
            if doc['status']!='running':return
            try:
                pipeline=publish_prepared_pipeline(self.db,doc['pipeline'])
                doc.update(status='done',batch_id=pipeline['batch_id'],stats=pipeline['stats'],message='Ontology ready.')
            except Exception as e:
                doc.update(status='error',message='CSV could not be published ('+type(e).__name__+'). The previous ontology remains active. Try uploading again.')
            self.db.put(doc,doc['revision'])

    def recover(self):
        for doc in self.db.list('ingestion:'):
            if doc['status']=='running':self.schedule(doc['job_id'])
