"""Trusted compiler/bundler, separate from generated packages and without app secrets."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading

_lock=threading.Lock()
_tools=None


def production_check(files):
    global _tools
    node=shutil.which('node');npm=shutil.which('npm')
    if not node or not npm:return {'status':'failed','output':'Node and npm are required to verify the production build.'}
    env={'PATH':os.environ.get('PATH',''),'HOME':tempfile.gettempdir(),'NODE_NO_WARNINGS':'1'}
    try:
        with _lock:
            if _tools is None:
                tools=Path(tempfile.mkdtemp(prefix='fernwood-toolchain-'))
                result=subprocess.run([npm,'install','--prefix',str(tools),'--ignore-scripts','--no-audit','--no-fund','typescript@5.9.3','vite@5.4.21'],env=env,capture_output=True,text=True,timeout=180)
                if result.returncode:return {'status':'failed','output':'Could not install the trusted build tools. Retry when the package registry is available.'}
                _tools=tools
        with tempfile.TemporaryDirectory(prefix='fernwood-production-') as directory:
            root=Path(directory)
            for path,content in files.items():
                if path=='README.md':continue
                dest=root/path;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(content)
            commands=[([node,str(_tools/'node_modules/typescript/bin/tsc'),'--noEmit','--target','es2022','--module','esnext','--moduleResolution','bundler','--allowImportingTsExtensions','--lib','es2022,dom','src/main.ts'],'TypeScript'),
                ([node,str(_tools/'node_modules/vite/bin/vite.js'),'build','--config',str(_tools/'vite.config.mjs')],'Production build')]
            # Generated files cannot supply executable build configuration or plugins.
            (_tools/'vite.config.mjs').write_text('export default {build:{minify:false}};\n')
            outputs=[]
            for command,label in commands:
                result=subprocess.run(command,cwd=root,env=env,capture_output=True,text=True,timeout=60)
                outputs.append(label+':\n'+result.stdout+result.stderr)
                if result.returncode:return {'status':'failed','output':'\n'.join(outputs)[-12000:]}
            return {'status':'passed','output':'\n'.join(outputs)[-12000:]}
    except subprocess.TimeoutExpired:return {'status':'failed','output':'Production verification timed out.'}
