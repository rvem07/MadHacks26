"""Real GitHub REST commits, restricted to the configured repository and a new branch."""
import hashlib
import os
import re
import threading

_publish_lock=threading.Lock()
from datetime import datetime,timezone
import httpx
from .build import GROUPS


class GitHubError(RuntimeError):
    def __init__(self,status):
        super().__init__('GitHub operation failed: HTTP '+str(status))
        self.status=status
        self.user_message={
            401:'GitHub rejected the token. Update the GITHUB_TOKEN secret and retry.',
            403:'GitHub denied writing to the repository (HTTP 403). Give the token Contents read/write access to the destination repository, then retry. Generated files are saved.',
            404:'GitHub could not access the repository or branch (HTTP 404). Check the destination and token repository access.'
        }.get(status,'GitHub operation failed (HTTP '+str(status)+'). Generated files are saved; retry publishing.')


class GitHubRepo:
    def __init__(self):
        self.url=os.getenv('GITHUB_REPO_URL','').removesuffix('.git').rstrip('/')
        match=re.fullmatch(r'https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)',self.url)
        token=os.getenv('GITHUB_TOKEN')
        if not match or not token: raise ValueError('Configure a repo-scoped GITHUB_TOKEN secret')
        self.root='https://api.github.com/repos/'+match[1]
        self.client=httpx.Client(headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'},timeout=30,follow_redirects=False)

    def call(self,method,path,body=None):
        r=self.client.request(method,self.root+path,json=body)
        if r.status_code>=400: raise GitHubError(r.status_code)
        return r.json()

    def head(self,branch): return self.call('GET','/git/ref/heads/'+branch)['object']['sha']

    def create_branch(self,branch,sha=None):
        if sha:
            self.call('POST','/git/refs',{'ref':'refs/heads/'+branch,'sha':sha})
            return sha
        default=self.call('GET','')['default_branch'];sha=self.head(default)
        self.call('POST','/git/refs',{'ref':'refs/heads/'+branch,'sha':sha})
        return sha

    def commit(self,branch,message,files):
        parent=self.head(branch);base=self.call('GET','/git/commits/'+parent)
        # Idempotent recovery if push succeeded before its Delta acknowledgement.
        if base['message']==message: return {'sha':parent,'commit_url':self.url+'/commit/'+parent,'message':message}
        if 'README.md' in files:
            original=self.call('GET','/git/trees/'+base['tree']['sha'])
            for entry in original.get('tree',[]):
                if entry['path'].lower()=='readme.md' and entry['path']!='README.md':files[entry['path']]=None
        tree=self.call('POST','/git/trees',{'base_tree':base['tree']['sha'],'tree':[
            {'path':path,'mode':'100644','type':'blob',**({'sha':None} if content is None else {'content':content})} for path,content in files.items()]})
        commit=self.call('POST','/git/commits',{'message':message,'tree':tree['sha'],'parents':[parent]})
        self.call('PATCH','/git/refs/heads/'+branch,{'sha':commit['sha'],'force':False})
        if self.head(branch)!=commit['sha']: raise RuntimeError('Remote branch verification failed')
        return {'sha':commit['sha'],'commit_url':self.url+'/commit/'+commit['sha'],'message':message}


    def reset_demo(self,run_id):
        if self.url!='https://github.com/Kman771/Test-Repo':
            raise ValueError('README-only cleanup is restricted to Kman771/Test-Repo')
        default=self.call('GET','')['default_branch'];parent=self.head(default)
        base=self.call('GET','/git/commits/'+parent)
        message='Prepare README-only demo base ['+run_id+']'
        if base['message']==message:return parent
        entries=self.call('GET','/git/trees/'+base['tree']['sha'])['tree']
        readmes=[e for e in entries if e['path'].lower()=='readme.md' and e['type']=='blob']
        entry=({'path':readmes[0]['path'],'mode':'100644','type':'blob','sha':readmes[0]['sha']} if readmes else
               {'path':'README.md','mode':'100644','type':'blob','content':'# Generated demo apps\nEach run is published to a separate agent-run branch.\n'})
        tree=self.call('POST','/git/trees',{'tree':[entry]})
        commit=self.call('POST','/git/commits',{'message':message,'tree':tree['sha'],'parents':[parent]})
        self.call('PATCH','/git/refs/heads/'+default,{'sha':commit['sha'],'force':False})
        if self.head(default)!=commit['sha']:raise RuntimeError('Default branch verification failed')
        return commit['sha']

    def preflight(self):
        from uuid import uuid4
        branch='agent-run-preflight-'+datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')+'-'+uuid4().hex[:6]
        self.create_branch(branch)
        created=self.commit(branch,'Verify Fernwood API commit',{'preflight.txt':'Fernwood GitHub API preflight\n'})
        removed=self.commit(branch,'Remove Fernwood API preflight file',{'preflight.txt':None})
        return {'status':'passed','path':'github_api','repo_url':self.url,'branch':branch,'commits':[created,removed]}


def publish(watcher,doc):
    with _publish_lock:
        return _publish(watcher,doc)


def _publish(watcher,doc):
    if not os.getenv('GITHUB_TOKEN'):
        doc['status']='error'
        watcher.event(doc,'error',{'message':'Source saved. Configure GITHUB_TOKEN with Contents read/write for the destination repository as an App secret, then retry to publish these files.','files':doc['artifacts'],'retryable':True})
        return
    repo=GitHubRepo()
    try:
        key='git-preflight:'+hashlib.sha256(repo.url.encode()).hexdigest()
        probe=watcher.db.get(key)
        if not probe or probe.get('status')!='passed':
            watcher.event(doc,'committing',{'message':'Testing GitHub API with a create/delete preflight on a separate branch.'})
            probe=repo.preflight()
            watcher.db.put(dict(probe,id=key),(watcher.db.get(key) or {}).get('revision',0))
            for commit in probe['commits']:watcher.event(doc,'committed',dict(commit,purpose='preflight'))
        if not doc.get('branch_created') and not doc.get('clean_base_sha'):
            watcher.event(doc,'committing',{'message':'Preparing a clean README-only starting point. Earlier demo branches are preserved.'})
            doc['clean_base_sha']=repo.reset_demo(doc['run_id'])
            saved=watcher.commit(doc);doc.clear();doc.update(saved)
        if not doc.get('branch'):
            doc['branch']='agent-run-'+datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')+'-'+doc['run_id'][:6]
            saved=watcher.commit(doc);doc.clear();doc.update(saved)
        if not doc.get('branch_created'):
            # Only recover an existing branch if its name belongs to this persisted run.
            try:repo.head(doc['branch'])
            except RuntimeError as e:
                if not str(e).endswith('404'):raise
                repo.create_branch(doc['branch'],doc['clean_base_sha'])
            doc['branch_created']=True;saved=watcher.commit(doc);doc.clear();doc.update(saved)
        files={r['path']:r['content'] for r in doc['artifacts']}
        for index,(title,paths) in enumerate(GROUPS):
            if len(doc['commits'])>index:continue
            message=f'{title} [Fernwood {doc["run_id"][:8]} step {index+1}]'
            watcher.event(doc,'committing',{'message':message,'files':paths,'branch':doc['branch']})
            commit=repo.commit(doc['branch'],message,{p:files[p] for p in paths})
            doc['commits'].append(commit);watcher.event(doc,'committed',commit)
        doc['status']='done'
        watcher.event(doc,'done',{'message':'Your app is ready. Source, tests, and build verified on GitHub.','file_count':len(doc['artifacts']),'repo_url':repo.url,
            'branch_url':repo.url+'/tree/'+doc['branch'],'commits':doc['commits'],'learned':doc['spec']['requirements'],'tests':doc['test_result']})
    finally:repo.client.close()
