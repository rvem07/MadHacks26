/* Fernwood: graph from Gold, every activity/answer/source/commit from physical events. */
const VERIFIED_DEMO_URL='https://github.com/Kman771/Test-Repo/tree/agent-run-20261115182956-659e30';
const $=id=>document.getElementById(id);
function h(tag,props={},...children){const el=tag==='svg'?document.createElementNS('http://www.w3.org/2000/svg',tag):document.createElement(tag);for(const [key,v] of Object.entries(props)){if(key.startsWith('on'))el.addEventListener(key.slice(2),v);else if(key==='class')el.className=v;else if(key==='checked'||key==='hidden')el[key]=v;else el.setAttribute(key,v)}for(const child of children.flat(Infinity)){if(child!==null&&child!==undefined)el.append(child instanceof Node?child:document.createTextNode(String(child)))}return el}
async function api(path,options={}){const r=await fetch('/api'+path,{...options,headers:{'Content-Type':'application/json'},body:options.body?JSON.stringify(options.body):undefined});if(!r.ok){const problem=await r.json().catch(()=>({}));throw new Error(typeof problem.detail==='string'?problem.detail:'Request failed ('+r.status+'). Check Setup or refresh.');}return r.json()}
const state={cy:null,graph:null,run:null,epoch:0,last:0,replay:false,pinned:false,active:null,location:null,stageEntered:0};
const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function link(url,title){try{const u=new URL(url);if(u.protocol!=='https:'||u.username||u.password)return h('span',{},title);return h('a',{href:u.href,target:'_blank',rel:'noopener noreferrer'},title)}catch{return h('span',{},title)}}
function error(e){$('error').textContent=e.message||String(e)}
async function boot(){
 document.body.replaceChildren(h('main',{class:'shell'},
  h('header',{},h('div',{class:'brand'},h('div',{class:'brand-mark','aria-hidden':'true'},'f'),h('div',{},h('p',{class:'eyebrow'},'FERNWOOD LABS'),h('h1',{},'Agent Watcher'))),
   h('nav',{class:'toolbar','aria-label':'Workspace'},h('span',{id:'mode',class:'badge'},'LIVE · DELTA'),h('button',{id:'uploadCsv'},'Upload CSV'),h('button',{id:'viewToggle'},'Data'),h('button',{id:'replay'},'Replay last run'),h('a',{id:'verifiedCode',class:'code-link',href:VERIFIED_DEMO_URL,target:'_blank',rel:'noopener noreferrer'},'View verified demo code ↗'),h('button',{id:'setup','aria-label':'Setup'},'⚙ Setup'))),
  h('div',{id:'stats',class:'stats'}),
  h('div',{id:'graphView',class:'workspace'},
   h('section',{class:'graph-pane','aria-label':'People graph'},h('div',{class:'graph-head'},h('h2',{},'Your team, connected.'),h('p',{id:'graphSummary'},'Your people. Shared knowledge. One agent.')),h('div',{id:'cy'}),h('svg',{id:'dangling','aria-hidden':'true'}),h('svg',{id:'travel','aria-hidden':'true'}),h('div',{id:'token',hidden:true}),h('div',{id:'card',hidden:true}),h('div',{class:'person-buttons'},h('select',{id:'personSelect','aria-label':'Inspect a person'},h('option',{value:''},'Inspect a person…'))),h('div',{class:'legend'},h('span',{},'Reports to'),h('span',{},'Works with'),h('span',{},'Needs review'))),
   h('aside',{class:'timeline'},h('div',{class:'panel-head'},h('h2',{},'Live activity'),h('small',{id:'stepCount'},'Ready')),h('ol',{id:'timeline','aria-live':'polite'},h('li',{class:'empty'},'Watch the agent discover what your team knows.','\n','Every step is saved in Unity Catalog.')))),
  h('section',{id:'dataView',class:'data-pane',hidden:true},h('h2',{},'From source to shared understanding'),h('p',{class:'muted'},'Actual pipeline rows · active published batch'),h('div',{id:'dataTabs',class:'tabs'}),h('div',{id:'tableOptions',class:'table-options'}),h('div',{id:'tableRows',class:'table-scroll'})),
  h('section',{class:'compose'},h('label',{for:'task'},'WHAT SHOULD THE AGENT BUILD?'),h('div',{class:'prompt-row'},h('textarea',{id:'task',rows:2},'Build an internal time-off request tracker for our team.'),h('button',{id:'run',class:'primary'},'Build & push ↗')),h('div',{class:'options'},h('label',{},h('input',{id:'dry',type:'checkbox',checked:false}),'Preview only · skip GitHub'),h('label',{},h('input',{id:'fresh',type:'checkbox'}),'Fresh persona answers'),h('span',{id:'runStatus',role:'status'},'Ready to listen.'))),h('p',{id:'error',role:'alert'}),
  h('dialog',{id:'uploadDialog',class:'dialog'},h('h2',{},'Build an ontology from CSV'),h('p',{class:'upload-help'},'Upload your team directory, reporting lines, expertise, and context. Collaborators are optional. New runs use the uploaded data.'),h('p',{class:'upload-help'},h('a',{href:'/api/samples/team.csv',download:'team.csv'},'Download sample CSV ↗')),h('label',{for:'csvFile'},'Team CSV · up to 1 MB'),h('input',{id:'csvFile',type:'file',accept:'.csv,text/csv'}),h('p',{class:'upload-help'},'Required columns: name, email, title, team, manager_email, expertise, context_notes. Optional: works_with (emails separated by semicolons), collaboration_context.'),h('p',{id:'uploadStatus',role:'status','aria-live':'polite'}),h('div',{class:'actions'},h('button',{id:'ingestCsv',class:'primary'},'Build ontology'),h('button',{onclick:()=>$('uploadDialog').close()},'Close'))),
  h('dialog',{id:'setupDialog',class:'dialog'},h('h2',{},'Setup'),h('div',{id:'setupBody'}),h('pre',{id:'connectionResult'}),h('div',{class:'actions'},h('button',{id:'testConnection'},'Test connection'),h('button',{onclick:()=>$('setupDialog').close()},'Done')))));
 $('run').disabled=$('replay').disabled=$('viewToggle').disabled=true;
  $('dry').onchange=()=>{$('run').textContent=$('dry').checked?'Preview files ↗':'Build & push ↗'};$('run').onclick=()=>start().catch(error);$('setup').onclick=()=>setup().catch(error);$('replay').onclick=()=>replay().catch(error);$('uploadCsv').onclick=()=>$('uploadDialog').showModal();$('ingestCsv').onclick=()=>ingestCsv();
 window.addEventListener('resize',()=>{state.cy?.resize();state.cy?.fit(undefined,65)});
 $('viewToggle').onclick=async()=>{const data=$('dataView').hidden;$('dataView').hidden=!data;$('graphView').hidden=data;$('viewToggle').textContent=data?'Graph':'Data';if(data)await dataTab('Raw CSV');else{state.cy?.resize();state.cy?.fit(undefined,65)}};
 for(const name of Object.keys(TABLES))$('dataTabs').append(h('button',{'data-tab':name,onclick:()=>dataTab(name).catch(error)},name));
 await initialGraph();
}
async function restoreSession(){
 const pendingUpload=localStorage.getItem('fernwood.ingestion');if(pendingUpload){$('uploadDialog').showModal();watchIngestion(pendingUpload).catch(error);return}
 const saved=localStorage.getItem('fernwood.run');if(saved){const prior=await api('/runs/'+encodeURIComponent(saved)).catch(()=>null);if(prior?.status==='running'){await loadGraph(prior.batch_id);state.run=saved;watch(++state.epoch).catch(error)}else if(prior){$('runStatus').textContent='Previous run saved · click Replay last run to review it';localStorage.removeItem('fernwood.run')}}
}
async function initialGraph(){
 $('runStatus').textContent='Loading team…';
 try{await loadGraph();$('run').disabled=$('replay').disabled=$('viewToggle').disabled=false;$('runStatus').textContent='Ready to listen.';$('error').textContent='';await restoreSession()}
 catch(e){error(e);$('runStatus').replaceChildren(h('button',{onclick:()=>initialGraph()},'Retry loading team'))}
}
const STAGE_DWELL={'stage:search':3000,'stage:code':3000};
const STAGES=[{id:'stage:search',label:'Web search',color:'#8b5cf6'},{id:'stage:code',label:'Coding',color:'#3b82f6'},{id:'stage:github',label:'GitHub',color:'#f59e0b'}];
async function loadGraph(batchId=null){
 if(batchId&&state.graph?.batch_id===batchId)return;
 const next=await api('/graph'+(batchId?'?batch_id='+encodeURIComponent(batchId):''));
 state.cy?.destroy();state.graph=next;state.active=null;state.location=null;$('token').hidden=true;$('travel').replaceChildren();$('card').hidden=true;
 $('personSelect').replaceChildren(h('option',{value:''},'Inspect a person…'));
 const s=next.stats;$('stats').replaceChildren(...[[s.raw_people_rows,'raw rows'],[s.unique_people,'people'],[s.fuzzy_duplicates_merged,'merged'],[s.exact_duplicates_collapsed,'collapsed'],[s.relationships_quarantined,'quarantined'],[s.valid_collab_edges,'collaborations']].map(([n,label])=>h('div',{class:'stat'},h('strong',{},n),h('small',{},label))));
 $('graphSummary').textContent=next.nodes.length+' people. Shared knowledge. One agent.';graph();
}
async function ingestCsv(){
 const file=$('csvFile').files[0];if(!file){$('uploadStatus').textContent='Choose a CSV file first.';return}
 if(file.size>1048576){$('uploadStatus').textContent='CSV must be at most 1 MB.';return}
 $('ingestCsv').disabled=true;$('run').disabled=true;$('uploadStatus').textContent='Validating CSV…';
 try{const job=await api('/ingestions',{method:'POST',body:{filename:file.name,csv_text:await file.text()}});localStorage.setItem('fernwood.ingestion',job.job_id);await watchIngestion(job.job_id)}
 catch(e){$('uploadStatus').textContent=e.message}
 finally{$('ingestCsv').disabled=false;$('run').disabled=false}
}
async function watchIngestion(jobId){
 $('ingestCsv').disabled=true;$('run').disabled=true;
 try{while(true){
  const job=await api('/ingestions/'+jobId);$('uploadStatus').textContent=job.message;
  if(job.status==='error'){localStorage.removeItem('fernwood.ingestion');throw new Error(job.message)}
  if(job.status==='done'){
   ++state.epoch;clearRun();state.run=null;state.replay=false;localStorage.removeItem('fernwood.run');localStorage.removeItem('fernwood.ingestion');
   await loadGraph(job.batch_id);$('mode').textContent='LIVE · DELTA';$('mode').classList.remove('replay');
   $('runStatus').textContent='CSV ontology ready · '+job.stats.unique_people+' people · '+job.stats.relationships_quarantined+' items need review';
   $('timeline').append(h('li',{class:'empty'},'Ontology built from '+job.filename+'. Enter a task and click Build & push.'));
   $('stepCount').textContent='Ready';$('uploadDialog').close();return;
  }
  await sleep(1500);
 }}finally{$('ingestCsv').disabled=false;$('run').disabled=false}
}
function graph(){
 const nodes=state.graph.nodes,ids=new Set(nodes.map(n=>n.id)),valid=state.graph.edges.filter(e=>ids.has(e.src)&&ids.has(e.dst));
 const depth=id=>{let cursor=id,seen=new Set();while(!seen.has(cursor)){seen.add(cursor);const e=valid.find(e=>e.src===cursor&&e.type==='reports_to'&&e.status==='valid');if(!e)break;cursor=e.dst}return seen.size-1};
 const ranks=new Map();for(const n of nodes){const d=depth(n.id);if(!ranks.has(d))ranks.set(d,[]);ranks.get(d).push(n)}
 const positions={};for(const [rank,people]of ranks){people.sort((a,b)=>a.team.localeCompare(b.team)||a.name.localeCompare(b.name));people.forEach((n,i)=>positions[n.id]={x:(i-(people.length-1)/2)*155,y:rank*175})}
 state.cy=cytoscape({container:$('cy'),elements:[...nodes.map(n=>({data:{...n,label:n.name.replace(' ','\n')+(n.needs_review?' ◦':'')},position:positions[n.id],classes:n.needs_review?'review':''})),...STAGES.map((n,i)=>({data:n,position:{x:430,y:i*175},classes:'stage'})),...valid.map(e=>({data:{...e,source:e.src,target:e.dst},classes:e.type+' '+e.status}))],layout:{name:'preset',padding:75},minZoom:.25,maxZoom:2,wheelSensitivity:.15,
 style:[{selector:'node',style:{label:'data(label)','text-wrap':'wrap','font-family':'-apple-system, sans-serif','font-size':11,'text-valign':'bottom','text-margin-y':12,color:'#698278','background-color':'#d0e3da','border-color':'#fff','border-width':3,width:43,height:43}},
 {selector:'node.review',style:{'border-color':'#c49b58','border-width':2,'border-style':'dashed'}},
 {selector:'node.stage',style:{'background-color':'data(color)',shape:'round-rectangle',width:48,height:48,color:'#58667a'}},
 {selector:'node.active',style:{'background-color':'#22c55e','underlay-color':'#39b99d','underlay-opacity':.18,'underlay-padding':14}},
 {selector:'node.answered',style:{'border-color':'#94a3b8'}},{selector:'node.stage.active',style:{'background-color':'data(color)'}},
 {selector:'edge',style:{width:1.3,'line-color':'#b3c5bc','curve-style':'bezier','target-arrow-shape':'triangle','target-arrow-color':'#b3c5bc','arrow-scale':.65}},
 {selector:'edge.works_with',style:{'target-arrow-shape':'none',width:1,opacity:.4,'line-color':'#98b6a6'}},
 {selector:'edge.quarantined',style:{'line-style':'dashed','line-color':'#bc9758','target-arrow-color':'#bc9758',opacity:.8}},
 {selector:'edge.traversed',style:{'line-color':'#0f766e','target-arrow-color':'#0f766e',width:3,opacity:1}}]});
 state.cy.on('mouseover','node',e=>{if(!state.pinned)card(e.target.id())});state.cy.on('mouseout','node',()=>{if(!state.pinned)$('card').hidden=true});state.cy.on('tap','node',e=>{state.pinned=true;card(e.target.id())});state.cy.on('tap',e=>{if(e.target===state.cy){state.pinned=false;$('card').hidden=true}});
 state.cy.on('pan zoom resize position',()=>{dangling();positionPulse()});dangling();
 for(const n of nodes)$('personSelect').append(h('option',{value:n.id},n.name));$('personSelect').onchange=()=>{state.pinned=true;card($('personSelect').value)};

}
function dangling(){const svg=$('dangling');svg.replaceChildren();for(const e of state.graph.edges.filter(e=>!state.cy.getElementById(e.dst).length)){const p=state.cy.getElementById(e.src).renderedPosition();if(!p)continue;const ns='http://www.w3.org/2000/svg',line=document.createElementNS(ns,'line'),label=document.createElementNS(ns,'text');for(const[k,v]of Object.entries({x1:p.x,y1:p.y,x2:p.x-40,y2:p.y-42,stroke:'#bc9758','stroke-dasharray':'4 4'}))line.setAttribute(k,v);label.setAttribute('x',p.x-75);label.setAttribute('y',p.y-49);label.setAttribute('font-size','9');label.setAttribute('fill','#ad8b51');label.textContent='Manager missing';svg.append(line,label)}}
function card(id){const n=state.graph.nodes.find(x=>x.id===id);if(!n)return;const managers=state.graph.edges.filter(e=>e.src===id&&e.type==='reports_to').map(e=>state.graph.nodes.find(p=>p.id===e.dst)?.name||e.dst);$('card').replaceChildren(h('small',{},n.team+(state.pinned?' · PINNED':'')),h('h2',{},n.name),h('p',{},n.title),h('small',{},'Manager: '+(managers.join(' / ')||'None — company lead')),h('div',{class:'tags'},n.expertise.split(',').map(x=>h('span',{class:'tag'},x.trim()))),n.needs_review?h('p',{class:'review'},'Needs review · '+n.review_reason.replaceAll('_',' ')):null);$('card').hidden=false}
async function setup(){$('setupDialog').showModal();$('setupBody').textContent='Loading configuration…';const s=await api('/setup');$('setupBody').replaceChildren(h('dl',{},...Object.entries(s).flatMap(([k,v])=>[h('dt',{},k.replaceAll('_',' ')),h('dd',{},String(v))])));$('testConnection').onclick=async()=>{try{$('testConnection').disabled=true;$('connectionResult').textContent='Checking from the App service principal…';$('connectionResult').textContent=JSON.stringify(await api('/setup/test',{method:'POST'}),null,2)}catch(e){error(e)}finally{$('testConnection').disabled=false}}}
const TABLES={'Raw CSV':['raw_files'],'Bronze':['bronze_org_chart','bronze_collaborations','bronze_person_context'],'Silver':['silver_people','silver_relationships','quarantine'],'Gold':['nodes','edges','pipeline_stats','quarantine']};
async function dataTab(name){document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('selected',b.dataset.tab===name));$('tableOptions').replaceChildren(...TABLES[name].map(t=>h('button',{onclick:()=>dataTable(t).catch(error)},t.replaceAll('_',' '))));await dataTable(TABLES[name][0])}
async function dataTable(table){$('tableRows').textContent='Querying Unity Catalog…';const {rows}=await api('/data/'+table);const keys=Array.from(new Set(rows.flatMap(Object.keys)));$('tableRows').replaceChildren(h('table',{},h('caption',{},table+' · '+rows.length+' rows'),h('thead',{},h('tr',{},keys.map(k=>h('th',{},k)))),h('tbody',{},rows.map(row=>h('tr',{},keys.map(k=>h('td',{},k==='content'?h('pre',{},row[k]):typeof row[k]==='object'?JSON.stringify(row[k]):String(row[k]??''))))))))}
function clearRun(){for(const stage of STAGES)state.cy.getElementById(stage.id).data('label',stage.label);state.last=0;state.active=null;state.location=null;state.stageEntered=0;$('timeline').replaceChildren();$('token').hidden=true;$('travel').replaceChildren();state.cy.elements().removeClass('active answered traversed');$('error').textContent=''}
async function start(){const epoch=++state.epoch;clearRun();state.replay=false;$('mode').textContent='LIVE · DELTA';$('mode').classList.remove('replay');$('run').disabled=true;try{const r=await api('/runs',{method:'POST',body:{task:$('task').value,dry_run:$('dry').checked,fresh:$('fresh').checked}});state.run=r.run_id;if(r.batch_id!==state.graph.batch_id)await loadGraph(r.batch_id);$('mode').textContent='LIVE · '+r.run_id.slice(0,8);localStorage.setItem('fernwood.run',r.run_id);await watch(epoch)}catch(e){error(e);$('run').disabled=false}}
async function watch(epoch){while(epoch===state.epoch){let events;try{events=await api('/runs/'+state.run+'/events?after='+state.last);$('error').textContent=''}catch(e){$('error').textContent='Reconnecting to the App…';await sleep(1000);continue}for(const e of events){if(epoch!==state.epoch)return;await renderEvent(e,epoch);state.last=e.seq;if((e.type==='done'||e.type==='error')&&e.seq===events[events.length-1].seq){$('run').disabled=false;return}}await sleep(500)}}
async function replay(id=null){const runs=await api('/runs');const run=id?runs.find(r=>r.run_id===id):(runs.find(r=>r.status==='done')||runs[0]);if(!run)throw new Error('No stored run to replay yet.');const epoch=++state.epoch;clearRun();await loadGraph(run.batch_id);if(epoch!==state.epoch)return;state.run=run.run_id;state.replay=true;$('mode').textContent='HISTORY · '+run.run_id.slice(0,8);$('timeline').append(h('li',{class:'empty'},'Historical run · '+new Date(run.created_at).toLocaleString()+' · This does not start a new build.'));$('mode').classList.add('replay');$('run').disabled=true;const events=await api('/runs/'+state.run+'/events');for(const e of events){if(epoch!==state.epoch)return;await renderEvent(e,epoch);state.last=e.seq}if(epoch===state.epoch){$('run').disabled=false;$('runStatus').textContent='Replay complete · stored events'}}
function positionPulse(){
 if(!state.active)return;
 const n=state.cy.getElementById(state.active),p=n.renderedPosition(),token=$('token');
 token.style.left=p.x+'px';token.style.top=p.y+'px';
 token.style.width=token.style.height=(n.renderedWidth()+10)+'px';
}
function activate(id){state.cy.nodes().removeClass('active');state.active=id;$('token').hidden=!id;$('token').classList.toggle('stage-pulse',!!id?.startsWith('stage:'));if(id){state.cy.getElementById(id).addClass('active');positionPulse()}}
async function settleStage(epoch){
 const until=state.stageEntered+(STAGE_DWELL[state.location]||0);
 while(epoch===state.epoch&&performance.now()<until)await sleep(Math.min(100,until-performance.now()));
 return epoch===state.epoch;
}
async function move(e,epoch){
 const p=e.payload,n=state.cy.getElementById(e.target_person);if(!n.length)return;
 if(state.location!==e.target_person&&!await settleStage(epoch))return;
 activate(null);
 const from=state.cy.getElementById(p.from_person||'');

 if(from.length&&!reduced){
  const ns='http://www.w3.org/2000/svg',flight=document.createElementNS(ns,'g'),path=document.createElementNS(ns,'path'),dot=document.createElementNS(ns,'circle');
  flight.setAttribute('class','travel-flight');path.setAttribute('class','travel-dash');path.setAttribute('fill','none');
  dot.setAttribute('class','travel-head');dot.setAttribute('r','5');flight.append(path,dot);$('travel').append(flight);
  const stageTrip=from.hasClass('stage')||n.hasClass('stage');
  const startTime=performance.now(),ms=stageTrip?950:450;
  await new Promise(resolve=>{function frame(now){
   if(epoch!==state.epoch){flight.remove();resolve();return}
   const start=from.renderedPosition(),end=n.renderedPosition();
   path.setAttribute('d',`M ${start.x} ${start.y} L ${end.x} ${end.y}`);
   const length=path.getTotalLength(),t=Math.min(1,(now-startTime)/ms),u=stageTrip?t*t*(3-2*t):t,head=length*u,tail=Math.min(65,head);
   const point=path.getPointAtLength(head);dot.setAttribute('cx',point.x);dot.setAttribute('cy',point.y);
   path.setAttribute('stroke-dasharray',`${tail} ${length+100}`);path.setAttribute('stroke-dashoffset',String(-(head-tail)));
   if(t<1)requestAnimationFrame(frame);else{flight.classList.add('fading');setTimeout(()=>flight.remove(),500);resolve()}
  }requestAnimationFrame(frame)});
 }
 if(epoch===state.epoch){state.location=e.target_person;state.stageEntered=performance.now();activate(e.target_person)}
}
async function publishSaved(button){
 button.disabled=true;button.textContent='Starting GitHub publish…';
 try{await api('/runs/'+state.run+'/publish',{method:'POST'});state.replay=false;$('mode').textContent='LIVE · DELTA';$('mode').classList.remove('replay');$('run').disabled=true;await watch(++state.epoch)}catch(e){error(e);button.disabled=false;button.textContent='Push these files to GitHub'}
}
async function visitStage(id,epoch){
 if(state.location!==id)await move({payload:{from_person:state.location},target_person:id},epoch);
 else activate(id);
}
const outputRequests=new Map();
async function showVerifiedOutput(container,rid){
 container.textContent='Checking this run’s GitHub branch…';
 try{if(!outputRequests.has(rid))outputRequests.set(rid,api('/runs/'+rid+'/output').finally(()=>outputRequests.delete(rid)));const result=await outputRequests.get(rid);
  if(!container.isConnected)return;
  container.replaceChildren(result.status==='available'?link(result.branch_url,'View this run’s generated code ↗'):h('p',{},result.message));
  if(result.status!=='available')container.append(h('button',{onclick:()=>showVerifiedOutput(container,rid)},'Check link again'));
 }catch{container.textContent='Could not verify the GitHub link. Refresh to try again.'}
}
const EVENT_TITLES={moving:'Finding the next expert',asking:'Gathering requirements',answered:'Insight collected',searching:'Checking technical references',search_results:'References found',search_unavailable:'Continuing with team context',spec_written:'Build plan ready',phase_started:'Work in progress',implementation:'Why the code looks this way',test_result:'Verification results',committing:'Publishing source',committed:'Saved to GitHub',retrying:'Retrying saved progress',done:'App ready',error:'Action needed'};
async function renderEvent(e,epoch){const p=e.payload||{},person=state.graph.nodes.find(n=>n.id===e.target_person);$('stepCount').textContent='Step '+e.seq;$('runStatus').textContent=(state.replay?'Replay · ':'')+(EVENT_TITLES[e.type]||e.type.replaceAll('_',' '));const li=h('li',{class:'event','data-type':e.type},h('div',{class:'event-top'},h('span',{},String(e.seq).padStart(2,'0')),h('span',{},(EVENT_TITLES[e.type]||e.type.replaceAll('_',' '))),person?h('span',{class:'person'},person.name):null));
 const phase=e.type==='phase_started'?p.stage:({searching:'search',search_results:'search',search_unavailable:'search',spec_written:'code',implementation:'code',test_result:'code',committing:'github',committed:'github'})[e.type];
 if(phase)await visitStage('stage:'+phase,epoch);
 if(epoch!==state.epoch)return;
 if(e.type==='implementation'){li.append(h('p',{},p.message));for(const d of p.decisions||[])li.append(h('div',{class:'decision'},h('strong',{},d.explanation),h('p',{class:'muted'},'From '+d.source_person_email.split('@')[0]+' · '+d.path),h('details',{},h('summary',{},'Requirement and code'),h('p',{},d.requirement),h('pre',{},d.excerpt))));}
 if(e.type==='phase_started'||e.type==='retrying')li.append(h('p',{},p.message));
 if(e.type==='moving'){li.append(h('p',{},p.adjacent?'Following a team relationship.':'Moving directly to the next expert.'));$('timeline').append(li);await move(e,epoch);return}
 if(e.type==='asking'){activate(e.target_person)}
 if(e.type==='asking')li.append(h('p',{},p.question),h('span',{class:'simulated'},'SIMULATED PERSONA'));
 if(e.type==='answered'){$('runStatus').textContent='Answer collected · choosing the next step…';state.cy.getElementById(e.target_person).removeClass('active').addClass('answered');li.append(h('span',{class:'simulated'},'SIMULATED'+(p.cached?' · CACHED':'')),h('p',{class:'answer'},p.answer?.split(/(?<=[.!?])\s/)[0]||p.answer),h('details',{},h('summary',{},'Full answer'),h('p',{},p.answer)))}
 if(e.type==='search_unavailable')state.cy.getElementById('stage:search').data('label','Web search\nUnavailable');
 if(e.type==='search_results')state.cy.getElementById('stage:search').data('label','Web search');
 if(e.type==='searching')li.append(h('p',{},p.query));
 if(e.type==='search_unavailable'||e.type==='error'){if(e.type==='error')activate(null);li.append(h('p',{},p.message));for(const file of p.files||[])li.append(h('details',{},h('summary',{},file.path),h('pre',{},file.content)));if(e.type==='error'&&p.retryable&&!state.replay)li.append(h('button',{onclick:async()=>{await api('/runs/'+state.run+'/retry',{method:'POST'});$('run').disabled=true;watch(++state.epoch).catch(error)}},'Retry run'));}
 if(e.type==='search_results')for(const source of p.sources||[])li.append(h('p',{},link(source.url,source.title)),h('p',{},source.key_points));
 if(e.type==='spec_written'){li.append(h('p',{},p.summary));for(const r of p.requirements||[])li.append(h('p',{},r.requirement,' ',h('button',{onclick:()=>{state.pinned=true;card(r.source_person_email)}},r.source_person_email.split('@')[0])));if(p.open_questions?.length)li.append(h('details',{},h('summary',{},'Open questions'),h('ul',{},p.open_questions.map(q=>h('li',{},q)))))}
 if(e.type==='test_result')li.append(h('p',{},p.status==='passed'?'Verification passed'+(p.production_build==='passed'?' · logic tests, TypeScript, and production build':' · logic tests'):p.status==='failed'?'Verification found an issue · correcting the source':'Verification unavailable'),h('details',{},h('summary',{},'Actual test output'),h('pre',{},p.output)));
 if(e.type==='committing')li.append(h('p',{},p.message));
 if(e.type==='committed')li.append(h('p',{},p.message?.replace(/ \[Fernwood.*$/, '')||'Commit saved'));
 if(e.type==='done'){if(!await settleStage(epoch))return;activate(null);li.classList.add('result-card');for(const old of document.querySelectorAll('#timeline [data-type=error]'))old.classList.add('resolved');if(p.outcome==='dry_run'){const button=h('button',{class:'primary'},'Push these files to GitHub');button.onclick=()=>publishSaved(button);li.append(button)}li.append(h('p',{},p.message||'Build complete.'),p.branch_url?h('div',{class:'verified-output','data-run':e.run_id||state.run}):null,p.branch_url&&p.branch_url!==VERIFIED_DEMO_URL?h('p',{},link(VERIFIED_DEMO_URL,'View verified presentation demo ↗')):null);if(p.file_count)li.append(h('p',{},p.file_count+' files generated · '+p.commits.length+' commits'));if(p.tests?.production_build==='passed')li.append(h('p',{class:'verified'},'✓ Tests passed · TypeScript checked · Production build passed'));if(p.commits?.length)li.append(h('details',{},h('summary',{},'Commit history'),p.commits.map(c=>h('p',{},c.message))));if(p.learned?.length)li.append(h('details',{},h('summary',{},'What the agent learned'),h('ul',{},p.learned.map(r=>h('li',{},r.requirement+' — '+r.source_person_email)))));for(const file of p.files||[])li.append(h('details',{},h('summary',{},file.path),h('pre',{},file.content)))}
 $('timeline').append(li);const output=li.querySelector('.verified-output');if(output)showVerifiedOutput(output,output.dataset.run);$('timeline').scrollTo({top:$('timeline').scrollHeight,behavior:reduced?'instant':'smooth'});
 if(e.type==='asking')await sleep(Math.min(e.duration_ms||1000,1000)); // Keep old replays at the current demo pace.
}
boot().catch(error);
