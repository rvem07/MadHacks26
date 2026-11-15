"""Real provider search; only retrieved provider URLs can become citations."""
import os
import re
from urllib.parse import urlsplit
import httpx
from .audit import now_iso


def safe_url(url):
    p=urlsplit(url)
    return p.scheme=='https' and bool(p.hostname) and not p.username and not p.password


def search(query):
    key=os.getenv('TAVILY_API_KEY')
    if not key: raise RuntimeError('Configure Tavily using secret TAVILY_API_KEY')
    r=httpx.post('https://api.tavily.com/search',headers={'Authorization':'Bearer '+key},
       json={'query':query,'max_results':3,'include_raw_content':True,'include_answer':False},timeout=40)
    r.raise_for_status()
    return [x for x in r.json().get('results',[]) if safe_url(x.get('url','')) and (x.get('raw_content') or x.get('content'))]


def research(watcher,doc):
    completed={e['payload']['query'] for e in doc['events'] if e['type'] in ['search_results','search_unavailable']}
    if 'queries' not in doc:
        result=watcher.model('research',{'task':doc['task'],'context':[r['answer'] for r in doc['run_context']]})
        queries=result.get('queries',[])
        if not isinstance(queries,list) or len(queries)>3: raise ValueError('Invalid research queries')
        doc['queries']=list(dict.fromkeys(q for q in queries if isinstance(q,str) and 0<len(q)<=300))
        # Task/context can contain personal names; do not send any employee identifiers to search.
        graph=watcher.db.graph(doc['batch_id'])
        private=[p['id'].lower() for p in graph['nodes']]+[p['name'].lower() for p in graph['nodes']]+['fernwood']
        doc['queries']=[q for q in doc['queries'] if not any(v in q.lower() for v in private) and '@' not in q]
        saved=watcher.commit(doc);doc.clear();doc.update(saved)
    for query in doc['queries'][:3]:
        if query in completed: continue
        watcher.event(doc,'searching',{'query':query})
        try:
            results=search(query); sources=[]
            for i,item in enumerate(results):
                summary=watcher.model('paraphrase',{'title':item.get('title',''),'retrieved_text':(item.get('raw_content') or item['content'])[:12000]})
                points=summary.get('key_points','')
                if not isinstance(points,str) or not points or len(points.split())>50: raise ValueError('Invalid paraphrase')
                row=dict(id=f"{doc['run_id']}:{len(doc['research'])}",run_id=doc['run_id'],query=query,
                    url=item['url'],title=item.get('title','Source')[:200],key_points=points,created_at=now_iso())
                doc['research'].append(row);sources.append(row)
            if not sources: raise ValueError('Provider returned no retrieved sources')
            watcher.event(doc,'search_results',{'query':query,'sources':sources})
        except Exception:
            watcher.event(doc,'search_unavailable',{'query':query,'message':'Real search unavailable. Configure Tavily secret TAVILY_API_KEY or check provider connectivity. No sources fabricated.'})
