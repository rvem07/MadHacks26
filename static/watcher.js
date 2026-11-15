/* Guided demo: existing graph renderer, SSE, and checkpoint API; three panels. */
const W = {run:null, pipeline:null, stream:null, epoch:0, shownRequest:null};
const watchApi = (path, options) => api('/api/watcher' + path, options);
const WILDLIFE_SCENARIO = 'wildlife_protection_v1';
const WILDLIFE_PROMPT = 'Build a secure application for our endangered-animal startup. Help conservation teams coordinate protection without exposing animal locations to hunters.';

async function watcherBoot() {
  document.title = 'Agent Watcher · Wildlife protection';
  document.body.className = 'wildlife-demo';
  document.body.replaceChildren(
    h('main', {class:'demo-shell'},
      h('section', {class:'card demo-prompt', 'aria-label':'Agent prompt'},
        h('div', {class:'demo-heading'}, h('h1', {}, 'Agent Watcher'), h('span', {class:'demo-caption'}, 'A guided wildlife-protection demo')),
        h('label', {for:'task'}, 'What should the agent help build?'),
        h('div', {class:'demo-compose'},
          h('textarea', {id:'task', rows:2}, WILDLIFE_PROMPT), h('button', {id:'start', class:'primary'}, 'Start demo')),
        h('p', {id:'runMeta', class:'muted', role:'status'}, 'The agent will ask the team for the decisions it needs.')),
      h('section', {class:'card demo-graph', 'aria-label':'Employee graph'},
        h('h2', {}, 'The team'), h('div', {id:'cy'}),
        h('p', {class:'demo-legend'}, 'Arrow → manager · Gold: waiting for an answer · Green: already consulted')),
      h('section', {class:'card demo-workflow', 'aria-label':'Workflow summary'},
        h('h2', {}, 'Workflow'), h('ol', {id:'log', class:'demo-summary', 'aria-live':'polite'}),
        h('div', {id:'demoQuestion'}), h('div', {id:'demoAnswer', hidden:true})))
  );
  W.pipeline = await watchApi('/pipeline');
  S.cy = cytoscape({container:$('cy'), wheelSensitivity:0.2, minZoom:0.3, maxZoom:2.5, style:graphStyle()});
  S.cy.style()
    .selector('node.consulted').style({'background-color':'#43816b', 'border-color':'#43816b', 'border-width':2})
    .selector('node.waiting').style({'background-color':'#e5af4b', 'border-color':'#a97416', 'border-width':4,
      'underlay-color':'#e5af4b','underlay-opacity':0.2,'underlay-padding':10})
    .update();
  watcherGraph(W.pipeline);
  $('log').append(h('li', {class:'muted'}, 'Start with an idea. Watch the agent consult operations, engineering, security, and field services.'));
  $('start').onclick = watcherStart;
  let saved;
  try { saved = localStorage.getItem('watcher.wildlife.run'); } catch (_) {}
  if (saved) {
    try { await watcherLoad(saved); }
    catch (_) { try { localStorage.removeItem('watcher.wildlife.run'); } catch (_) {} }
  }
  window.addEventListener('resize', () => { S.cy.resize(); S.cy.fit(undefined, 35); });
}

function watcherGraph(pipeline) {
  const cy = S.cy;
  cy.elements().remove();
  cy.add(pipeline.nodes.map(p => ({data:{id:p.id, display:p.name, label:p.name}, classes:'person'})));
  cy.add(pipeline.edges.map(e => ({data:{id:e.id, source:e.src, target:e.dst, type:'reports to'}})));
  cy.layout({name:'breadthfirst', directed:true, spacingFactor:1.15, padding:35}).run();
}

async function watcherStart() {
  $('start').disabled = true;
  $('task').disabled = true;
  try {
    const run = await watchApi('/runs', {method:'POST', body:{task:$('task').value,
      requester:'maria.alvarez@badgernet.com', scenario:WILDLIFE_SCENARIO}});
    try { localStorage.setItem('watcher.wildlife.run', run.run_id); } catch (_) {}
    await watcherLoad(run.run_id);
  } catch(e) {
    $('runMeta').textContent = e.message;
    $('start').disabled = false;
    $('task').disabled = false;
  }
}

async function watcherLoad(id) {
  const epoch = ++W.epoch;
  if (W.stream) W.stream.close();
  const run = await watchApi('/runs/' + id);
  if (run.scenario !== WILDLIFE_SCENARIO) throw new Error('This run belongs to another workflow.');
  const pipeline = await watchApi('/pipeline?batch=' + encodeURIComponent(run.batch_id));
  if (epoch !== W.epoch) return;
  W.pipeline = pipeline;
  watcherGraph(pipeline);
  W.run = run;
  $('task').value = run.task;
  watcherRender();
  if (['done','cancelled'].includes(run.status)) return;
  const stream = new EventSource(`/api/watcher/runs/${id}/stream?after=${run.events.length}`);
  W.stream = stream;
  stream.addEventListener('state', event => {
    if (epoch !== W.epoch) return;
    const next = JSON.parse(event.data);
    if (next.revision < W.run.revision) return;
    W.run = next;
    watcherRender();
    if (['done','cancelled'].includes(next.status)) stream.close();
  });
  stream.onerror = () => {
    if (epoch === W.epoch && !['done','cancelled'].includes(W.run.status))
      $('runMeta').textContent = 'Reconnecting… Your answers are saved.';
  };
}

function watcherRender() {
  const run = W.run;
  if (!run) return;
  if (W.renderedRun === run.run_id) {
    for (const ev of run.events.filter(e => e.step > W.renderedStep && e.edge)) {
      S.cy.getElementById(ev.edge).stop().animate({style:{width:6}}, {duration:250})
        .animate({style:{width:2}}, {duration:450});
    }
  }
  W.renderedRun = run.run_id;
  W.renderedStep = run.events.length;
  const pending = run.requests.find(r => r.status === 'pending');
  const terminal = ['done','cancelled'].includes(run.status);
  $('start').disabled = !terminal;
  $('task').disabled = !terminal;
  $('start').textContent = terminal ? 'Run again' : 'Demo in progress';
  $('runMeta').textContent = run.execution_error || run.projection_error
    ? 'The workflow could not continue. Your progress is saved; please check the app connection.'
    : run.status === 'done' ? 'Application plan ready — the team’s decisions are summarized below.'
    : pending ? `Waiting for ${personName(pending.asked_person_email)} · ${Object.keys(run.scenario_decisions || {}).length} of 5 decisions clarified`
    : 'The agent is reviewing the answer…';
  const lines = [h('li', {}, 'Planning a secure application to protect endangered wildlife.')];
  for (const req of run.requests) {
    const name = personName(req.asked_person_email);
    if (req.status === 'answered') {
      lines.push(h('li', {class:req.resolved ? 'summary-resolved' : 'summary-followup'},
        h('strong', {}, name), h('span', {}, req.resolved ? ` confirmed: ${req.answer}` : ` replied: ${req.answer}. The agent needs a more specific answer.`)));
    } else if (req.status === 'pending') {
      const decision = {users:'primary users', sightings:'sighting collection', location_access:'location access', alerts:'field alerts', pilot:'the pilot rollout'}[req.decision_id];
      lines.push(h('li', {class:'summary-current'}, `${req.followup ? 'Following up with' : 'Consulting'} ${name} about ${decision}.`));
    }
  }
  if (run.status === 'done') lines.push(h('li', {class:'summary-resolved'}, 'All five decisions are clarified. The application plan is ready.'));
  $('log').replaceChildren(...lines);
  S.cy.elements().removeClass('active consulted waiting trav');
  for (const req of run.requests.filter(r => r.status === 'answered')) S.cy.getElementById(req.asked_person_email).addClass('consulted');
  for (const ev of run.events.filter(e => e.edge)) S.cy.getElementById(ev.edge).addClass('trav');
  if (pending) S.cy.getElementById(pending.asked_person_email).addClass('waiting');
  if (pending && W.shownRequest !== pending.request_id) {
    W.shownRequest = pending.request_id;
    watcherQuestion(pending);
  } else if (!pending) {
    W.shownRequest = null;
    $('demoQuestion').replaceChildren();
  }
  $('demoAnswer').hidden = !run.answer;
  if (run.answer) $('demoAnswer').replaceChildren(h('h3', {}, 'Your application specification'), h('p', {}, run.answer));
}

function personName(email) {
  return W.pipeline.nodes.find(p => p.id === email)?.name || email;
}

function watcherQuestion(request) {
  const person = W.pipeline.nodes.find(p => p.id === request.asked_person_email);
  const form = h('form', {class:'demo-question'});
  form.append(h('p', {class:'question-person'}, `Answer for ${person.name} · ${person.props.title}`));
  const fieldset = h('fieldset', {}, h('legend', {}, request.question));
  const explanation = h('textarea', {id:'otherExplanation', rows:3, placeholder:'Explain your answer…', hidden:true, 'aria-label':'Other explanation'});
  for (const choice of request.choices) {
    const input = h('input', {type:'radio', name:'choice', value:choice.id, required:true});
    input.onchange = () => {
      explanation.hidden = choice.id !== 'other';
      explanation.required = choice.id === 'other';
      if (choice.id === 'other') explanation.focus();
    };
    fieldset.append(h('label', {class:'demo-choice'}, input, h('span', {}, choice.label)));
  }
  const submit = h('button', {class:'primary', type:'submit'}, 'Submit answer');
  const error = h('p', {class:'answer-error', role:'alert'});
  form.append(fieldset, explanation, error, submit);
  form.onsubmit = async e => {
    e.preventDefault();
    const selected = form.querySelector('input[name="choice"]:checked');
    if (!selected) return;
    submit.disabled = true;
    error.textContent = '';
    try {
      const run = await watchApi(`/pending_requests/${request.request_id}/answer`, {method:'POST', body:{
        person:request.asked_person_email, option_id:selected.value, explanation:selected.value === 'other' ? explanation.value : ''}});
      // Apply only the active run's response; reload is unnecessary and would interrupt the stream.
      if (W.run?.run_id === run.run_id) { W.run = run; watcherRender(); }
    } catch(e) { error.textContent = e.message; submit.disabled = false; }
  };
  $('demoQuestion').replaceChildren(form);
}
