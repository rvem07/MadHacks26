# Databricks notebook source
"""Probe Git CLI on Databricks compute with the linked OAuth identity. No credentials printed."""
import json
import subprocess
from pathlib import Path
from datetime import datetime,timezone

root=Path('/Workspace/Users/kaashish.v@gmail.com/.bundle/Test-Repo')
report={'runtime':'Databricks serverless Job','path':str(root),'checks':[]}
def git(*args):
    p=subprocess.run(['git',*args],cwd=root,capture_output=True,text=True,timeout=60)
    if p.returncode:
        # Do not return stderr (credential helpers may include sensitive diagnostics).
        message=p.stderr.lower()
        reason=next((x for x in ['could not read username','authentication failed','permission denied','403','credential','repository not found','not a git repository'] if x in message),'unclassified git failure')
        raise RuntimeError('git '+args[0]+' failed with exit '+str(p.returncode)+': '+reason)
    return p.stdout.strip()
try:
    current=git('branch','--show-current')
    if current.startswith('agent-run-preflight-') and (root/'preflight.txt').exists() and (root/'preflight.txt').read_text()=='Fernwood Databricks Git preflight\n':
        git('rm','preflight.txt')
        git('-c','user.name=Fernwood Agent','-c','user.email=agent@fernwoodlabs.io','commit','-m','Clean up failed preflight probe')
        git('switch','main')
    report['original_branch']=git('branch','--show-current')
    if git('status','--porcelain'): raise RuntimeError('Git folder contains uncommitted changes; probe refused')
    branch='agent-run-preflight-'+datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
    git('switch','-c',branch)
    (root/'preflight.txt').write_text('Fernwood Databricks Git preflight\n')
    git('add','preflight.txt')
    git('-c','user.name=Fernwood Agent','-c','user.email=agent@fernwoodlabs.io','commit','-m','Verify Databricks Git write and push')
    git('push','origin',branch)
    report['create_commit']=git('rev-parse','HEAD')
    if git('ls-remote','origin','refs/heads/'+branch).split()[0]!=report['create_commit']: raise RuntimeError('Remote verification failed')
    git('rm','preflight.txt')
    git('-c','user.name=Fernwood Agent','-c','user.email=agent@fernwoodlabs.io','commit','-m','Remove preflight test file')
    git('push','origin',branch)
    report['cleanup_commit']=git('rev-parse','HEAD')
    git('switch',report['original_branch'])
    report.update(status='passed',branch=branch)
except Exception as e:
    try:
        if (root/'preflight.txt').exists() and git('branch','--show-current').startswith('agent-run-preflight-'):
            git('rm','preflight.txt')
            git('-c','user.name=Fernwood Agent','-c','user.email=agent@fernwoodlabs.io','commit','-m','Remove preflight file after unsuccessful push')
        if report.get('original_branch'): git('switch',report['original_branch'])
    except Exception: pass
    report.update(status='failed',error=str(e) if isinstance(e,RuntimeError) else type(e).__name__)
dbutils.notebook.exit(json.dumps(report))
