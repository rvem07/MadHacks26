"""Preflight synthesis with actual served model and prior UC answers (no writes)."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))


def main():
    from afd.sentinel import SentinelWatcher
    from afd.watcher import model_json
    from afd.watchstore import DeltaDocuments
    from afd.synthesis import synthesize
    parser=argparse.ArgumentParser()
    parser.add_argument('--warehouse-id',required=True)
    args=parser.parse_args()
    def inspect_model(messages):
        result=model_json(messages)
        if messages[0]['content'].startswith('SYNTHESIZE_SENTINEL_PLAN:'):
            print(json.dumps({'draft_summary':result.get('summary')}),flush=True)
        return result
    watcher=SentinelWatcher(DeltaDocuments(args.warehouse_id,'workspace','sentinel_gold'),model=inspect_model,background=False)
    run=next(r for r in watcher.runs() if r['status']=='done' and r.get('run_all') and len(r['requests'])==5)
    run['task'] += ' Also identify the fourteen external viewers by name.'
    plan,corrections=synthesize(watcher,run,watcher.pipeline(run['batch_id']))
    print(json.dumps({'result':'passed','source_run_id':run['run_id'],'plan':plan,'owner_corrections':corrections},indent=2))


if __name__=='__main__':
    main()
