/* Sentinel uses the existing API helpers, graph styles and SSE checkpoint transport.
 * All graph activity, workflow lines and answers come from persisted events.
 */
const T = {run:null, pipeline:null, stream:null, epoch:0, lastStep:0};
const sentinelApi = (path, options) => api('/api/watcher' + path, options);
const SENTINEL_PROMPT = 'A user reports exact GPS coordinates of a black rhino sighting may have been visible on a public map layer for ~2 hours. What do we need to find out, and who approves telling our ranger partners?';

async function sentinelBoot() {
  document.body.replaceChildren(h('main', {class:'sentinel-shell'},
    h('header', {}, h('div', {}, h('small', {}, 'WILDGUARD LABS / SENTINEL'), h('h1', {}, 'Agent Watcher')),
      h('span', {class:'live-chip'}, 'LIVE · Unity Catalog')),
    h('section', {id:'stats', class:'sentinel-stats', 'aria-label':'Pipeline statistics'}),
    h('section', {class:'card sentinel-compose'},
      h('label', {for:'task'}, 'Investigate a location exposure'),
      h('textarea', {id:'task', rows:3}, SENTINEL_PROMPT),
      h('div', {class:'sentinel-buttons'}, h('button', {id:'start', class:'primary'}, 'Start run'),
        h('button', {id:'runAll'}, 'Run all'), h('button', {id:'reset'}, 'Reset')),
      h('p', {id:'runMeta', role:'status'}, 'Ready. Start run consults Ben → Mei → Carlos → Elena. Run all adds Nadia and asks all five concurrently.')),
    h('div', {class:'sentinel-columns'},
      h('section', {class:'card sentinel-graph'}, h('h2', {}, 'People, topics & reporting'), h('div', {id:'cy'}),
        h('p', {class:'muted'}, 'Gold glow: waiting · Green: answered · Red border: needs review · Arrows: relationship direction')),
      h('section', {class:'card sentinel-events'}, h('h2', {}, 'Live investigation'),
        h('ol', {id:'log', 'aria-live':'polite'}), h('div', {id:'manualAnswers'}), h('div', {id:'summary'}))),
    h('section', {class:'card sentinel-manual'}, h('h2', {}, 'Manual mode'),
      h('p', {class:'muted'}, 'Choose people before starting a run. Their requests wait for a typed answer. Other people answer from their factual context.'),
      h('div', {id:'manualToggles'})),
    h('p', {id:'error', role:'alert'})));
  T.pipeline = await sentinelApi('/pipeline');
  if (T.pipeline.storage_mode !== 'delta') document.querySelector('.live-chip').textContent = 'TEST FIXTURE · no live Databricks';
  if (typeof cytoscape === 'undefined') throw new Error('Cytoscape failed to load. Check access to cdn.jsdelivr.net.');
  S.cy = cytoscape({container:$('cy'), wheelSensitivity:0.15, minZoom:0.15, maxZoom:3, style:graphStyle()});
  S.cy.style()
    .selector('node').style({'label':'data(display)', 'text-wrap':'wrap', 'text-max-width':110, 'font-size':10})
    .selector('node.topic').style({'shape':'round-rectangle', 'background-color':'#d7e6ec', 'width':25, 'height':25})
    .selector('node.team').style({'shape':'diamond', 'background-color':'#cdd7c7', 'width':22, 'height':22})
    .selector('node.review').style({'border-color':'#c7584c', 'border-width':4, 'border-style':'dashed'})
    .selector('node.waiting').style({'background-color':'#e7b858', 'border-color':'#bd8520', 'border-width':4,
      'underlay-color':'#efc35f', 'underlay-opacity':0.4, 'underlay-padding':12})
    .selector('node.consulted').style({'background-color':'#62a58b', 'border-color':'#2c785b', 'border-width':3})
    .selector('edge').style({'label':'data(label)', 'font-size':7, 'curve-style':'bezier', 'target-arrow-shape':'triangle'})
    .selector('edge.member_of').style({'line-style':'dotted', 'opacity':0.3})
    .selector('edge.traversed').style({'line-color':'#b88a30', 'target-arrow-color':'#b88a30', 'width':3}).update();
  sentinelGraph(T.pipeline);
  const preferences = await sentinelApi('/manual');
  for (const person of T.pipeline.nodes.filter(p => p.type === 'person')) {
    const input = h('input', {type:'checkbox', 'aria-label':'Manual mode for ' + person.name, checked:preferences.manual.includes(person.id)});
    input.onchange = async () => {
      try { await sentinelApi('/manual', {method:'PUT', body:{person:person.id, manual:input.checked}}); }
      catch (error) { input.checked = !input.checked; $('error').textContent = error.message; }
    };
    $('manualToggles').append(h('label', {}, input, person.name, person.props.needs_review ? ' · needs review' : ''));
  }
  $('start').onclick = () => sentinelStart(false);
  $('runAll').onclick = () => sentinelStart(true);
  $('reset').onclick = sentinelReset;
  const saved = localStorage.getItem('sentinel.run');
  if (saved) {
    try { await sentinelLoad(saved); }
    catch (error) { $('error').textContent = error.message; }
  }
  window.addEventListener('resize', () => { S.cy.resize(); S.cy.fit(undefined, 35); });
}

function sentinelGraph(pipeline) {
  S.cy.elements().remove();
  S.cy.add(pipeline.nodes.map(n => ({data:{id:n.id, display:n.name + (n.props?.needs_review ? '\nneeds review' : '')},
    classes:n.type + (n.props?.needs_review ? ' review' : '')})));
  S.cy.add(pipeline.edges.map(e => ({data:{id:e.id, source:e.src, target:e.dst,
    label:e.type.replaceAll('_',' ')}, classes:e.type})));
  S.cy.layout({name:'cose', animate:false, padding:35, nodeRepulsion:15000, idealEdgeLength:95}).run();
  const stats = pipeline.stats;
  $('stats').replaceChildren(...[
    `${stats.raw_people} raw → ${stats.unique_people} people`, `${stats.duplicates_merged} duplicate merged`,
    `${stats.quarantined_count} quarantined issues`, `${stats.violations_quarantined} records retained`
  ].map(text => h('span', {}, text)));
}

async function sentinelStart(runAll) {
  $('error').textContent = '';
  sentinelBusy(true);
  try {
    const run = await sentinelApi('/runs', {method:'POST', body:{task:$('task').value, requester:'demo user', run_all:runAll}});
    localStorage.setItem('sentinel.run', run.run_id);
    await sentinelLoad(run.run_id);
  } catch (error) { $('error').textContent = error.message; sentinelBusy(false); }
}

function sentinelBusy(busy) {
  for (const id of ['start','runAll','task']) $(id).disabled = busy;
  for (const input of $('manualToggles').querySelectorAll('input')) input.disabled = busy;
}

async function sentinelLoad(id) {
  const epoch = ++T.epoch;
  T.stream?.close();
  T.lastStep = 0;
  const run = await sentinelApi('/runs/' + id);
  run.events = await sentinelApi(`/runs/${id}/events`);
  const pipeline = await sentinelApi('/pipeline?batch=' + encodeURIComponent(run.batch_id));
  if (epoch !== T.epoch) return;
  T.pipeline = pipeline;
  sentinelGraph(pipeline);
  T.run = run;
  $('task').value = run.task;
  sentinelRender();
  if (['done','cancelled'].includes(run.status)) return;
  const stream = new EventSource(`/api/watcher/runs/${id}/stream?after=${run.events.length}`);
  T.stream = stream;
  stream.addEventListener('state', event => {
    if (epoch !== T.epoch) return;
    const next = JSON.parse(event.data);
    if (next.revision < T.run.revision) return;
    T.run = next;
    sentinelRender();
    if (['done','cancelled'].includes(next.status)) stream.close();
  });
  stream.onerror = () => { if (epoch === T.epoch && !['done','cancelled'].includes(T.run.status)) $('runMeta').textContent = 'Reconnecting to saved events…'; };
}

function sentinelRender() {
  const run = T.run;
  const events = run.events;
  const outstanding = new Map();
  const answered = new Set();
  let summary = '';
  S.cy.elements().removeClass('waiting consulted traversed');
  for (const event of events) {
    if (event.action === 'ask_human') outstanding.set(event.result.request_id, event);
    if (['human_answer','persona_answer'].includes(event.action)) {
      outstanding.delete(event.result.request_id);
      answered.add(event.target_node);
    }
    if (event.action === 'conclude') summary = event.result_summary;
    if (event.action === 'cancel') outstanding.clear();
    if (event.edge) {
      const edge = S.cy.getElementById(event.edge).addClass('traversed');
      if (event.step > T.lastStep) edge.stop().animate({style:{width:7}}, {duration:250}).animate({style:{width:3}}, {duration:500});
    }
  }
  for (const person of answered) S.cy.getElementById(person).addClass('consulted');
  for (const event of outstanding.values()) S.cy.getElementById(event.target_node).addClass('waiting');
  const newStep = events.at(-1)?.step || 0;
  if (newStep !== T.lastStep) {
    $('log').replaceChildren(...events.map(event => h('li', {class:event.action, 'data-step':event.step},
      h('small', {}, `${event.step} · ${event.actor} · ${event.state}`), h('p', {}, event.result_summary))));
  }
  T.lastStep = newStep;
  $('summary').replaceChildren(...(summary ? [h('h3', {}, 'Incident summary'), h('p', {}, summary)] : []));
  const manual = [...outstanding.values()].filter(event => event.result.manual);
  const currentForms = new Set([...$('manualAnswers').querySelectorAll('form')].map(f => f.dataset.request));
  const wantedForms = new Set(manual.map(e => e.result.request_id));
  for (const form of $('manualAnswers').querySelectorAll('form')) if (!wantedForms.has(form.dataset.request)) form.remove();
  for (const event of manual) if (!currentForms.has(event.result.request_id)) sentinelQuestion(event);
  const terminal = ['done','cancelled'].includes(run.status);
  sentinelBusy(!terminal);
  $('runMeta').textContent = run.execution_error || run.projection_error || `${run.status.replaceAll('_',' ')} · ${outstanding.size} awaiting answers · ${answered.size} people consulted`;
}

function sentinelQuestion(event) {
  const name = T.pipeline.nodes.find(p => p.id === event.target_node)?.name || event.target_node;
  const input = h('textarea', {rows:3, required:true, 'aria-label':'Answer for ' + name});
  const submit = h('button', {type:'submit', class:'primary'}, 'Submit answer for ' + name);
  const form = h('form', {'data-request':event.result.request_id}, h('h3', {}, name + ' · manual mode'),
    h('p', {}, event.result.question), input, submit);
  form.onsubmit = async e => {
    e.preventDefault(); submit.disabled = true;
    try {
      await sentinelApi(`/pending_requests/${event.result.request_id}/answer`, {method:'POST', body:{person:event.target_node, answer:input.value}});
      // SSE supplies committed events; never fabricate an answer in the UI.
    } catch (error) { $('error').textContent = error.message; submit.disabled = false; }
  };
  $('manualAnswers').append(form);
}

async function sentinelReset() {
  try {
    if (T.run && !['done','cancelled'].includes(T.run.status)) await sentinelApi(`/runs/${T.run.run_id}/reset`, {method:'POST'});
    ++T.epoch; T.stream?.close(); T.run = null; T.lastStep = 0;
    localStorage.removeItem('sentinel.run');
    S.cy.elements().removeClass('waiting consulted traversed');
    for (const id of ['log','manualAnswers','summary']) $(id).replaceChildren();
    $('runMeta').textContent = 'Ready for a new investigation. Previous events remain in Delta.';
    $('error').textContent = ''; sentinelBusy(false);
  } catch (error) { $('error').textContent = error.message; }
}
