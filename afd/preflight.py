"""Checks executed by the App identity; never return credentials or response bodies."""
import io
import os
import re
from databricks.sdk.service.workspace import ImportFormat

import httpx


def check_connection(write_probe=False):
    from databricks.sdk import WorkspaceClient
    w = WorkspaceClient()
    path = os.getenv('GIT_FOLDER_PATH', '').rstrip('/')
    repo = os.getenv('GITHUB_REPO_URL', '')
    result = {'git_folder_path': path, 'repo_url': repo, 'runtime': 'Databricks App',
              'path': 'github_api' if os.getenv('GITHUB_TOKEN') else 'github_api_pending_token', 'checks': [], 'search_configured': bool(os.getenv('TAVILY_API_KEY'))}
    if path:
        try:
            w.workspace.get_status(path)
            result['checks'].append({'check': 'folder_read', 'status': 'passed'})
            if write_probe:
                probe = path + '/fernwood-preflight-write.txt'
                try:
                    w.workspace.get_status(probe)
                except Exception as e:
                    if getattr(e, 'error_code', '') != 'RESOURCE_DOES_NOT_EXIST':
                        raise
                else:
                    raise ValueError('Probe already exists; refusing overwrite')
                w.workspace.upload(probe, io.BytesIO(b'Fernwood app identity write probe\n'), overwrite=False, format=ImportFormat.AUTO)
                w.workspace.delete(probe)
                result['checks'].append({'check': 'folder_write_cleanup', 'status': 'passed'})
        except Exception as e:
            # Structured SDK error codes convey permissions without leaking request credentials.
            result['checks'].append({'check': 'folder_access', 'status': 'failed',
                'error': getattr(e, 'error_code', type(e).__name__), 'detail': 'The workspace API could not write into this Git CLI folder.' if getattr(e,'error_code','')=='RESOURCE_DOES_NOT_EXIST' else 'Workspace file operation failed.',
                'needed': 'Grant the App service principal CAN EDIT for workspace writes and CAN MANAGE for Git operations.'})
    else:
        result['checks'].append({'check': 'folder_access', 'status': 'not configured'})
    result['checks'].append({'check': 'repos_commit_push', 'status': 'unsupported',
        'detail': 'The Repos REST API exposes create/read/update/delete, not commit or push. Git CLI access on supported compute requires a separate runtime check.'})
    try:
        response = httpx.get('https://api.github.com', timeout=15, follow_redirects=False)
        response.raise_for_status()
        result['checks'].append({'check': 'github_network', 'status': 'passed'})
        match=re.fullmatch(r'https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)',repo.removesuffix('.git').rstrip('/'))
        if match:
            token=os.getenv('GITHUB_TOKEN')
            response=httpx.get('https://api.github.com/repos/'+match[1],headers={'Authorization':'Bearer '+token} if token else {},timeout=15,follow_redirects=False)
            result['checks'].append({'check':'github_repo_read','status':'passed' if response.status_code==200 else 'failed','http_status':response.status_code,'authenticated':bool(token)})
        result['checks'].append({'check':'github_write_credentials','status':'configured; write probe required' if os.getenv('GITHUB_TOKEN') else 'missing repo-scoped GITHUB_TOKEN secret'})
    except Exception as e:
        result['checks'].append({'check': 'github_network', 'status': 'failed', 'error': type(e).__name__})
    if write_probe and os.getenv('GITHUB_TOKEN'):
        from .git_output import GitHubRepo
        api=GitHubRepo()
        try: result['api_commit_push']=api.preflight()
        except Exception as e: result['api_commit_push']={'status':'failed','error':type(e).__name__}
        finally: api.client.close()
    elif write_probe and os.getenv('GIT_PREFLIGHT_JOB_ID'):
        try:
            result['git_probe_run_id']=w.jobs.run_now(job_id=int(os.environ['GIT_PREFLIGHT_JOB_ID'])).run_id
        except Exception as e:
            result['git_probe_error']=getattr(e,'error_code',type(e).__name__)
    return result
