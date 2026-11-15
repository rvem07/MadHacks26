/* Sentinel uses the existing API helpers and SSE checkpoint transport.
 * All graph activity, workflow lines and answers come from persisted events.
 *
 * Presentation model: events are *played* in order. Each event's motion (the agent
 * travelling an edge, gliding to a person, a reply ripple) runs first, then its state
 * (node classes, timeline row) is applied, so the graph, the agent and the timeline
 * always agree. History loaded on page open is applied instantly.
 */
const T = {run:null, pipeline:null, stream:null, epoch:0, lastStep:0, planKey:null, selectedSource:null, downloadUrl:null,
  shown:0, queued:0, playing:false};
const sentinelApi = (path, options) => api('/api/watcher' + path, options);
const SENTINEL_PROMPT = 'A user reports exact GPS coordinates of a black rhino sighting may have been visible on a public map layer for ~2 hours. What do we need to find out, and who approves telling our ranger partners?';
const REDUCED = matchMedia('(prefers-reduced-motion: reduce)');
const TERMINAL = ['done', 'cancelled'];

/* ------------------------------------------------------------------ boot + layout */
async function sentinelBoot() {
  document.body.replaceChildren(
    h('header', {class:'sn-bar'},
      h('div', {class:'sn-bar-inner'},
        h('div', {class:'sn-brand'}, h('span', {class:'sn-mark', 'aria-hidden':'true'}),
          h('div', {}, h('h1', {}, 'Agent Watcher'), h('small', {}, 'WildGuard Labs · Sentinel'))),
        h('div', {class:'sn-bar-status'},
          h('span', {id:'statusPill', class:'sn-pill', 'data-state':'idle'}, h('i'), h('span', {}, 'Ready')),
          h('span', {class:'live-chip'}, 'Live · Unity Catalog')))),
    h('main', {class:'sentinel-shell'},
      h('section', {class:'card sentinel-compose'},
        h('label', {for:'task'}, 'Investigate a location exposure'),
        h('textarea', {id:'task', rows:2}, SENTINEL_PROMPT),
        h('div', {class:'sn-compose-row'},
          h('div', {class:'sentinel-buttons'},
            h('button', {id:'start', class:'primary'}, 'Start run'),
            h('button', {id:'runAll'}, 'Run all'),
            h('button', {id:'reset', class:'quiet'}, 'Reset')),
          h('p', {id:'runMeta', role:'status'}, 'Start run asks Ben, Mei, Carlos and Elena in turn. Run all also asks Nadia, all at once.')),
        h('div', {class:'sentinel-manual'},
          h('div', {class:'sn-manual-head'}, h('h2', {}, 'Answer manually'),
            h('p', {class:'muted'}, 'Chosen people wait for your typed answer. Everyone else answers from their factual context.')),
          h('div', {id:'manualToggles'}))),
      h('div', {class:'sentinel-columns'},
        h('section', {class:'card sentinel-graph'},
          h('div', {class:'sn-card-head'}, h('h2', {}, 'People, topics & reporting'),
            h('ul', {class:'sn-legend', 'aria-label':'Legend'},
              h('li', {'data-k':'waiting'}, 'Waiting'), h('li', {'data-k':'consulted'}, 'Answered'),
              h('li', {'data-k':'review'}, 'Needs review'), h('li', {'data-k':'owner'}, 'Plan owner'))),
          h('div', {class:'sn-stage'}, h('div', {id:'cy'}), h('canvas', {id:'agentLayer', 'aria-hidden':'true'}),
            h('div', {id:'tip', class:'sn-tip', role:'tooltip'})),
          h('div', {class:'sn-graph-foot'},
            h('p', {class:'muted sn-hint'}, 'Hover any person, topic, team or line for details.'),
            h('section', {id:'stats', class:'sentinel-stats', 'aria-label':'Pipeline statistics'}))),
        h('section', {class:'card sentinel-events'},
          h('div', {class:'sn-card-head'}, h('h2', {}, 'Live investigation'), h('span', {id:'stepCount', class:'muted'})),
          h('div', {class:'sn-scroll', id:'logScroll'},
            h('ol', {id:'log', 'aria-live':'polite'}),
            h('p', {id:'logEmpty', class:'muted'}, 'Steps appear here as the agent works.'),
            h('div', {id:'manualAnswers'}), h('div', {id:'summary'})))),
      h('section', {class:'card sentinel-plan', 'aria-label':'Build plan'},
        h('div', {class:'plan-heading'}, h('h2', {}, 'Plan'),
          h('a', {id:'downloadPlan', class:'sn-button-link', hidden:true}, 'Download plan.md')),
        h('p', {id:'planStatus', role:'status', class:'muted'}, 'A cited build plan appears after all answers are collected.'),
        h('article', {id:'planMarkdown', 'aria-label':'Sentinel build plan'})),
      h('p', {id:'error', role:'alert'})));
  document.body.classList.add('sentinel');

  T.pipeline = await sentinelApi('/pipeline');
  if (T.pipeline.storage_mode !== 'delta') {
    const chipEl = document.querySelector('.live-chip');
    chipEl.textContent = 'Test fixture · no live Databricks'; chipEl.classList.add('fixture');
  }
  if (typeof cytoscape === 'undefined') throw new Error('Cytoscape failed to load. Check access to cdn.jsdelivr.net.');
  S.cy = cytoscape({container:$('cy'), wheelSensitivity:0.2, minZoom:0.3, maxZoom:1.6, autoungrabify:true,
    boxSelectionEnabled:false, style:sentinelStyle()});
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { S.cy.style(sentinelStyle()); Agent.draw(); });
  Agent.init();
  Tip.init();
  sentinelGraph(T.pipeline);

  const preferences = await sentinelApi('/manual');
  for (const person of T.pipeline.nodes.filter(p => p.type === 'person')) {
    const input = h('input', {type:'checkbox', 'aria-label':'Manual mode for ' + person.name, checked:preferences.manual.includes(person.id)});
    input.onchange = async () => {
      try { await sentinelApi('/manual', {method:'PUT', body:{person:person.id, manual:input.checked}}); }
      catch (error) { input.checked = !input.checked; $('error').textContent = error.message; }
    };
    $('manualToggles').append(h('label', {class:'sn-toggle' + (person.props?.needs_review ? ' review' : '')},
      input, h('span', {}, person.name), person.props?.needs_review ? h('em', {}, 'needs review') : null));
  }
  $('start').onclick = () => sentinelStart(false);
  $('runAll').onclick = () => sentinelStart(true);
  $('reset').onclick = sentinelReset;
  let saved = null;
  try { saved = localStorage.getItem('sentinel.run'); } catch (_) { /* storage unavailable */ }
  if (saved) {
    try { await sentinelLoad(saved); }
    catch (error) { $('error').textContent = error.message; }
  }
  let resizeTimer;
  new ResizeObserver(() => {
    Agent.resize();
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { S.cy.resize(); S.cy.animate({fit:{padding:36}}, {duration:REDUCED.matches ? 0 : 320, easing:'ease-out-cubic'}); }, 120);
  }).observe($('cy'));
}

/* ------------------------------------------------------------------ graph styling */
const ICONS = {
  topic: '<path d="M12.6 2.6A2 2 0 0 0 11.2 2H4a2 2 0 0 0-2 2v7.2a2 2 0 0 0 .6 1.4l8.7 8.7a2.4 2.4 0 0 0 3.4 0l6.6-6.6a2.4 2.4 0 0 0 0-3.4z"/><circle cx="7.5" cy="7.5" r="1.2" fill="COLOR"/>',
  team: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8"/>',
};
const icon = (kind, color) => 'data:image/svg+xml;utf8,' + encodeURIComponent(
  `<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ICONS[kind].replaceAll('COLOR', color)}</svg>`);
const tok = name => getComputedStyle(document.body).getPropertyValue(name).trim();

function sentinelStyle() {
  const c = {ink:tok('--sn-ink'), muted:tok('--sn-muted'), surface:tok('--sn-surface'), line:tok('--sn-edge'),
    node:tok('--sn-node'), nodeRing:tok('--sn-node-ring'), topic:tok('--sn-topic'), topicInk:tok('--sn-topic-ink'),
    team:tok('--sn-team'), teamInk:tok('--sn-team-ink'), agent:tok('--sn-agent'), ok:tok('--sn-ok'), wait:tok('--sn-wait'),
    bad:tok('--sn-bad'), owner:tok('--sn-owner'), source:tok('--sn-source')};
  const ease = {'transition-duration':'320ms', 'transition-timing-function':'ease-out-cubic'};
  return [
    {selector:'node', style:{width:46, height:46, 'border-width':1.5, 'border-color':c.nodeRing, 'background-color':c.node,
      label:'', 'font-family':'-apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, sans-serif', color:c.ink,
      'overlay-opacity':0, 'transition-property':'background-color, border-color, border-width, opacity, width, height, underlay-opacity, underlay-padding', ...ease}},
    {selector:'node.person', style:{label:'data(initials)', 'text-valign':'center', 'text-halign':'center', 'font-size':14,
      'font-weight':600, color:c.ink}},
    {selector:'node.topic', style:{shape:'round-rectangle', width:40, height:40, 'background-color':c.topic, 'border-width':1,
      'border-color':c.topicInk, 'border-opacity':0.25, 'background-image':icon('topic', c.topicInk), 'background-width':'60%', 'background-height':'60%'}},
    {selector:'node.topic.blocked', style:{opacity:0.55}},
    {selector:'node.team', style:{shape:'round-rectangle', width:44, height:44, 'background-color':c.team, 'border-width':1,
      'border-color':c.teamInk, 'border-opacity':0.25, 'background-image':icon('team', c.teamInk), 'background-width':'60%', 'background-height':'60%'}},
    {selector:'node.review', style:{'border-color':c.bad, 'border-width':2.5, 'border-style':'dashed'}},
    {selector:'node.consulted', style:{'background-color':c.ok, 'border-color':c.ok, color:'#fff'}},
    {selector:'node.waiting', style:{'background-color':c.wait, 'border-color':c.wait, color:'#1d1d1f'}},
    {selector:'node.plan-owner', style:{'underlay-color':c.owner, 'underlay-opacity':0.28, 'underlay-padding':9, 'underlay-shape':'ellipse'}},
    {selector:'node.source-highlight', style:{'border-color':c.source, 'border-width':4,
      'underlay-color':c.source, 'underlay-opacity':0.3, 'underlay-padding':14, 'underlay-shape':'ellipse'}},
    {selector:'node.focus', style:{'border-color':c.agent, 'border-width':3}},
    {selector:'node.hover', style:{width:54, height:54, 'border-width':3, 'border-color':c.agent}},
    {selector:'node.topic.hover, node.team.hover', style:{width:46, height:46}},
    {selector:'edge', style:{width:1.5, 'line-color':c.line, 'curve-style':'bezier', 'target-arrow-shape':'triangle',
      'target-arrow-color':c.line, 'arrow-scale':0.8, label:'', 'overlay-opacity':0,
      'transition-property':'line-color, target-arrow-color, width, opacity', ...ease}},
    {selector:'edge.member_of', style:{'line-style':'dotted', 'target-arrow-shape':'none', opacity:0.22, width:1}},
    {selector:'edge.responsible_for', style:{'line-style':'dashed', 'line-dash-pattern':[5, 4], 'arrow-scale':0.6}},
    {selector:'edge.traversed', style:{'line-color':c.agent, 'target-arrow-color':c.agent, width:3, opacity:1, 'line-style':'solid'}},
    {selector:'edge.hover', style:{width:4, 'line-color':c.agent, 'target-arrow-color':c.agent, opacity:1}},
    {selector:'.faded', style:{opacity:0.18}},
  ];
}

/** Deterministic org layout: one column per team, people stacked by reporting depth, topics under their owner. */
function orgLayout(pipeline) {
  const COL_GAP = 44, PERSON_W = 82, ROW_H = 124, TOPIC_DY = 66, TOPIC_W = 54, TEAM_DY = -96;
  const people = pipeline.nodes.filter(n => n.type === 'person');
  const manager = {}, team = {}, owned = {};
  for (const e of pipeline.edges) {
    if (e.type === 'reports_to') manager[e.src] = e.dst;
    if (e.type === 'member_of') team[e.src] = e.dst;
    if (e.type === 'responsible_for') (owned[e.src] ||= []).push(e.dst);
  }
  const depthOf = (id, seen = new Set()) => (manager[id] && !seen.has(id)) ? 1 + depthOf(manager[id], seen.add(id)) : 0;
  const maxDepth = Math.max(0, ...people.filter(p => manager[p.id]).map(p => depthOf(p.id)));
  // People with a quarantined (missing) manager sit on the lowest row rather than pretending to lead.
  const depth = id => manager[id] ? depthOf(id) : people.some(p => manager[p.id] === id) ? 0 : maxDepth;
  const teams = pipeline.nodes.filter(n => n.type === 'team');
  const columns = teams.map(t => ({id:t.id, members:people.filter(p => team[p.id] === t.id)}));
  const loose = people.filter(p => !team[p.id]);
  if (loose.length) columns.push({id:null, members:loose});
  for (const col of columns) col.top = Math.min(...col.members.map(p => depth(p.id)), 99);
  columns.sort((a, b) => a.top - b.top || b.members.length - a.members.length);
  const pos = {};
  let x = 0;
  for (const col of columns) {
    const rows = {};
    for (const p of col.members.sort((a, b) => (manager[a.id] || '').localeCompare(manager[b.id] || '') || a.name.localeCompare(b.name)))
      (rows[depth(p.id)] ||= []).push(p);
    const slotW = p => Math.max(PERSON_W, (owned[p.id]?.length || 0) * TOPIC_W);
    const width = Math.max(PERSON_W, ...Object.values(rows).map(r => r.reduce((w, p) => w + slotW(p), 0)));
    for (const [d, row] of Object.entries(rows)) {
      let cx = x + (width - row.reduce((w, p) => w + slotW(p), 0)) / 2;
      for (const p of row) {
        const w = slotW(p), px = cx + w / 2, py = Number(d) * ROW_H;
        pos[p.id] = {x:px, y:py};
        (owned[p.id] || []).forEach((t, i, all) => { pos[t] = {x:px + (i - (all.length - 1) / 2) * TOPIC_W, y:py + TOPIC_DY}; });
        cx += w;
      }
    }
    if (col.id) pos[col.id] = {x:x + width / 2, y:TEAM_DY};
    x += width + COL_GAP;
  }
  // Topics nobody owns go to the far right, flagged by their own styling.
  const orphans = pipeline.nodes.filter(n => n.type === 'topic' && !pos[n.id]);
  orphans.forEach((t, i) => { pos[t.id] = {x, y:maxDepth * ROW_H + TOPIC_DY - i * TOPIC_W}; });
  return pos;
}

function sentinelGraph(pipeline) {
  Tip.hide();
  S.cy.elements().remove();
  S.cy.add(pipeline.nodes.map(n => ({data:{id:n.id, name:n.name, initials:initials(n.name), type:n.type},
    classes:[n.type, n.props?.needs_review ? 'review' : '', n.type === 'topic' && n.props?.routing_allowed === false ? 'blocked' : ''].join(' ')})));
  S.cy.add(pipeline.edges.map(e => ({data:{id:e.id, source:e.src, target:e.dst, type:e.type}, classes:e.type})));
  S.cy.layout({name:'preset', positions:orgLayout(pipeline), fit:false}).run();
  S.cy.fit(undefined, 30);
  const stats = pipeline.stats;
  $('stats').replaceChildren(...[
    [`${stats.raw_people} raw → ${stats.unique_people} people`, 'Identity resolution: raw org-chart rows to unique people'],
    [`${stats.duplicates_merged} duplicate merged`, 'Alias rows folded into one person'],
    [`${stats.quarantined_count} quarantined issues`, 'Issue groups excluded from agent routing'],
    [`${stats.violations_quarantined} records retained`, 'Quarantined records kept for review'],
  ].map(([text, title]) => h('span', {class:'sn-stat', title}, text)));
  Agent.reset();
}

const initials = name => (name || '?').split(/\s+/).filter(Boolean).map(w => w[0]).filter(ch => /\p{L}/u.test(ch))
  .slice(0, 2).join('').toUpperCase() || '?';
const personName = id => T.pipeline?.nodes.find(n => n.id === id)?.name || id;

/* ------------------------------------------------------------------ hover cards */
const Tip = {
  el:null, target:null, visible:false,
  init() {
    this.el = $('tip');
    const cy = S.cy;
    cy.on('mouseover', 'node, edge', e => this.show(e.target));
    cy.on('mouseout', 'node, edge', () => this.hide());
    cy.on('tap', 'node, edge', e => this.show(e.target));
    cy.on('tap', e => { if (e.target === cy) this.hide(); });
    cy.on('pan zoom', () => { if (this.target) this.place(this.target, true); });
  },
  show(target) {
    const cy = S.cy;
    cy.elements().removeClass('hover faded');
    target.addClass('hover');
    const keep = target.isNode() ? target.closedNeighborhood() : target.union(target.connectedNodes());
    cy.elements().not(keep).addClass('faded');
    this.el.replaceChildren(...(target.isNode() ? nodeCard(target) : edgeCard(target)));
    const first = !this.visible;
    this.target = target; this.visible = true;
    this.place(target, first);
    this.el.classList.add('show');
  },
  hide() {
    if (!this.el) return;
    S.cy?.elements().removeClass('hover faded');
    this.el.classList.remove('show');
    this.visible = false; this.target = null;
  },
  place(target, instant) {
    const stage = this.el.parentElement.getBoundingClientRect();
    const p = target.isNode() ? target.renderedPosition() : target.renderedMidpoint();
    const offset = target.isNode() ? target.renderedOuterHeight() / 2 + 12 : 14;
    const w = this.el.offsetWidth || 260, ht = this.el.offsetHeight || 120;
    let x = p.x - w / 2, y = p.y - offset - ht, below = false;
    if (y < 8) { y = p.y + offset; below = true; }
    x = Math.max(8, Math.min(stage.width - w - 8, x));
    y = Math.max(8, Math.min(stage.height - ht - 8, y));
    this.el.style.transformOrigin = `${p.x - x}px ${below ? 0 : ht}px`;
    this.el.classList.toggle('instant', !!instant);
    this.el.style.setProperty('--x', x + 'px');
    this.el.style.setProperty('--y', y + 'px');
  },
};

function runFacts(nodeId) {
  const facts = {question:null, answer:null, waiting:false};
  if (!T.run) return facts;
  for (const event of T.run.events.slice(0, T.shown)) {
    if (event.target_node !== nodeId) continue;
    if (event.action === 'ask_human') { facts.question = event.result?.question || event.result_summary; facts.waiting = true; }
    if (['human_answer', 'persona_answer'].includes(event.action)) { facts.answer = event.result_summary; facts.waiting = false; }
  }
  return facts;
}

function nodeCard(node) {
  const data = T.pipeline.nodes.find(n => n.id === node.id()) || {};
  const props = data.props || {};
  const rows = [];
  const status = node.hasClass('waiting') ? ['wait', 'Waiting for an answer'] : node.hasClass('consulted') ? ['ok', 'Answered'] : null;
  if (data.type === 'person') {
    rows.push(h('header', {}, h('span', {class:'sn-tip-kind'}, 'Person'), status ? h('span', {class:'sn-tip-status ' + status[0]}, status[1]) : null),
      h('strong', {}, data.name), h('p', {class:'sn-tip-sub'}, [props.title, props.team].filter(Boolean).join(' · ')),
      h('p', {class:'sn-tip-mono'}, data.id));
    const owns = node.outgoers('edge.responsible_for').targets().map(t => t.data('name'));
    if (owns.length) rows.push(h('p', {}, h('span', {class:'sn-tip-label'}, 'Owns '), owns.join(', ')));
    const manager = node.outgoers('edge.reports_to').targets()[0];
    if (manager) rows.push(h('p', {}, h('span', {class:'sn-tip-label'}, 'Reports to '), manager.data('name')));
    if (props.needs_review) {
      const reasons = [...new Set((T.pipeline.quarantine || []).filter(q => q.group_id === data.id || q.raw_record?.email === data.id)
        .map(q => q.reason.replaceAll('_', ' ')))];
      rows.push(h('p', {class:'sn-tip-warn'}, 'Needs review' + (reasons.length ? ': ' + reasons.join(', ') : '') + '. Excluded from routing.'));
    }
    const facts = runFacts(data.id);
    if (facts.question) rows.push(h('div', {class:'sn-tip-quote'}, h('span', {class:'sn-tip-label'}, 'Asked'), h('p', {}, clip(facts.question, 180))));
    if (facts.answer) rows.push(h('div', {class:'sn-tip-quote ok'}, h('span', {class:'sn-tip-label'}, 'Answered'), h('p', {}, clip(facts.answer, 220))));
  } else if (data.type === 'topic') {
    const owners = node.incomers('edge.responsible_for').sources().map(s => s.data('name'));
    rows.push(h('header', {}, h('span', {class:'sn-tip-kind'}, 'Topic')), h('strong', {}, capitalize(data.name)),
      h('p', {}, h('span', {class:'sn-tip-label'}, 'Owner '), owners.join(', ') || 'No owner'),
      h('p', {class:props.routing_allowed === false ? 'sn-tip-warn' : 'sn-tip-sub'},
        props.routing_allowed === false ? 'Not routable: the agent will not send questions here.' : 'The agent can route questions here.'));
  } else {
    const members = node.incomers('edge.member_of').sources().map(s => s.data('name'));
    rows.push(h('header', {}, h('span', {class:'sn-tip-kind'}, 'Team')), h('strong', {}, data.name || node.id()),
      h('p', {class:'sn-tip-sub'}, `${members.length} ${members.length === 1 ? 'member' : 'members'}`),
      h('p', {}, members.join(', ')));
  }
  return rows;
}

const EDGE_TEXT = {reports_to:'reports to', member_of:'is a member of', responsible_for:'is responsible for'};
function edgeCard(edge) {
  const data = T.pipeline.edges.find(e => e.id === edge.id()) || {};
  const src = edge.source().data('name'), dst = edge.target().data('name');
  const rows = [h('header', {}, h('span', {class:'sn-tip-kind'}, (data.type || '').replaceAll('_', ' ')),
      edge.hasClass('traversed') ? h('span', {class:'sn-tip-status agent'}, 'Traversed') : null),
    h('strong', {}, `${src} ${EDGE_TEXT[data.type] || '→'} ${data.type === 'responsible_for' ? capitalize(dst) : dst}`)];
  if (data.source?.length) rows.push(h('p', {class:'sn-tip-mono'}, 'Source: ' + data.source.join(', ')));
  if (data.confidence !== undefined) rows.push(h('p', {class:'sn-tip-sub'}, `Confidence ${Math.round(data.confidence * 100)}%`));
  const steps = (T.run?.events || []).slice(0, T.shown).filter(e => e.edge === edge.id());
  for (const step of steps.slice(-2)) rows.push(h('div', {class:'sn-tip-quote agent'}, h('span', {class:'sn-tip-label'}, 'Step ' + step.step), h('p', {}, step.result_summary)));
  return rows;
}
const clip = (s, n) => (s && s.length > n ? s.slice(0, n - 1).trimEnd() + '…' : s || '');
const capitalize = s => s ? s[0].toUpperCase() + s.slice(1) : s;

/* ------------------------------------------------------------------ agent motion layer */
const Agent = {
  canvas:null, ctx:null, dpr:1, raf:0, gen:0,
  pos:null, visible:0, wantVisible:false, at:null,
  trail:null, glide:null, ripples:[],
  init() {
    this.canvas = $('agentLayer'); this.ctx = this.canvas.getContext('2d');
    this.resize();
    S.cy.on('render', () => this.kick());
  },
  resize() {
    const r = this.canvas.getBoundingClientRect();
    this.dpr = window.devicePixelRatio || 1;
    this.canvas.width = Math.max(1, r.width * this.dpr); this.canvas.height = Math.max(1, r.height * this.dpr);
    this.kick();
  },
  reset() {
    this.gen++;
    this.pos = null; this.at = null; this.trail = null; this.glide = null; this.ripples = []; this.visible = 0; this.wantVisible = false;
    S.cy?.nodes().removeClass('focus');
    this.kick();
  },
  kick() { if (!this.raf) this.raf = requestAnimationFrame(t => this.frame(t)); },
  frame(now) {
    this.raf = 0;
    const live = this.draw(now);
    if (live) this.kick();
  },
  // Model <-> rendered conversion (pan/zoom aware).
  toScreen(p) { const z = S.cy.zoom(), pan = S.cy.pan(); return {x:p.x * z + pan.x, y:p.y * z + pan.y}; },
  rim(node, toward) {
    // Point on the node's rim facing `toward` (model coords).
    const c = node.position(), r = node.width() / 2 + 3;
    const dx = toward.x - c.x, dy = toward.y - c.y, d = Math.hypot(dx, dy) || 1;
    return {x:c.x + dx / d * r, y:c.y + dy / d * r};
  },
  // Critically damped spring (damping ratio 1): starts with zero velocity, no overshoot.
  spring(t) { const w = 9; return t >= 1 ? 1 : 1 - (1 + w * t) * Math.exp(-w * t) + t * (1 + w) * Math.exp(-w); },
  run(duration, step) {
    const gen = this.gen;
    return new Promise(resolve => {
      if (REDUCED.matches || duration <= 0) { step(1); this.kick(); resolve(); return; }
      const start = performance.now();
      const tick = now => {
        if (gen !== this.gen) { resolve(); return; }
        const t = Math.min(1, (now - start) / duration);
        step(this.spring(t));
        this.kick();
        if (t < 1) requestAnimationFrame(tick); else resolve();
      };
      requestAnimationFrame(tick);
    });
  },
  setFocus(node) {
    S.cy.nodes('.focus').not(node).removeClass('focus');
    node.addClass('focus');
    this.at = node.id();
  },
  async glideTo(node, speed) {
    this.wantVisible = true;
    const target = node.position();
    if (!this.pos) {
      this.pos = this.rim(node, {x:target.x + 1, y:target.y - 1});
      this.setFocus(node); this.kick();
      return;
    }
    if (this.at === node.id()) return;
    const from = {...this.pos}, to = this.rim(node, from);
    const dist = Math.hypot(to.x - from.x, to.y - from.y);
    // Arc gently so a glide never reads as travelling along an edge.
    const mx = (from.x + to.x) / 2, my = (from.y + to.y) / 2, nx = -(to.y - from.y) / (dist || 1), ny = (to.x - from.x) / (dist || 1);
    const ctrl = {x:mx + nx * dist * 0.18, y:my + ny * dist * 0.18};
    this.glide = {from, ctrl, to, p:0};
    await this.run(Math.min(820, 380 + dist * 0.9) * speed, p => {
      this.glide.p = p;
      const q = 1 - p;
      this.pos = {x:q * q * from.x + 2 * q * p * ctrl.x + p * p * to.x, y:q * q * from.y + 2 * q * p * ctrl.y + p * p * to.y};
    });
    this.glide = null;
    this.setFocus(node);
  },
  async travel(edge, toNode, speed) {
    const fromNode = edge.source().id() === toNode.id() ? edge.target() : edge.source();
    await this.glideTo(fromNode, speed);
    const a = this.rim(fromNode, toNode.position()), b = this.rim(toNode, fromNode.position());
    const dist = Math.hypot(b.x - a.x, b.y - a.y);
    this.trail = {a, b, p:0};
    await this.run(Math.min(1150, 520 + dist * 2.2) * speed, p => {
      this.trail.p = p;
      this.pos = {x:a.x + (b.x - a.x) * p, y:a.y + (b.y - a.y) * p};
    });
    edge.addClass('traversed');
    this.setFocus(toNode);
    // Let the canvas trail fade into the edge's own (transitioning) colour.
    const trail = this.trail;
    trail.fade = performance.now();
    setTimeout(() => { if (this.trail === trail) { this.trail = null; this.kick(); } }, 360);
  },
  ripple(node, color) {
    this.ripples.push({id:node.id(), color, t0:performance.now()});
    this.kick();
  },
  draw(now = performance.now()) {
    const ctx = this.ctx, cy = S.cy;
    if (!ctx || !cy) return false;
    const W = this.canvas.width, H = this.canvas.height, dpr = this.dpr;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, W, H);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const zoom = cy.zoom();
    const agent = tok('--sn-agent'), wait = tok('--sn-wait');
    let live = false;

    // Fade the agent in/out smoothly.
    const target = this.wantVisible && this.pos ? 1 : 0;
    this.visible += (target - this.visible) * (REDUCED.matches ? 1 : 0.16);
    if (Math.abs(target - this.visible) > 0.01) live = true; else this.visible = target;

    // Waiting nodes breathe with expanding rings (static ring when motion is reduced).
    for (const node of cy.nodes('.waiting')) {
      const c = node.renderedPosition(), r = node.renderedWidth() / 2;
      if (REDUCED.matches) {
        ring(ctx, c, r + 6, wait, 0.5, 2);
      } else {
        for (const phase of [0, 0.5]) {
          const p = ((now / 1700) + phase) % 1;
          ring(ctx, c, r + 3 + p * 22 * Math.min(1.4, zoom + 0.3), wait, 0.55 * (1 - p), 2);
        }
        live = true;
      }
    }
    // Answer ripples.
    this.ripples = this.ripples.filter(rp => now - rp.t0 < 900);
    for (const rp of this.ripples) {
      const node = cy.getElementById(rp.id); if (!node.length) continue;
      const p = (now - rp.t0) / 900, e = 1 - Math.pow(1 - p, 3);
      ring(ctx, node.renderedPosition(), node.renderedWidth() / 2 + e * 30, rp.color, 0.7 * (1 - p), 3);
      live = true;
    }
    // The growing trail: drawn from the departure rim to the agent's live position.
    if (this.trail) {
      const a = this.toScreen(this.trail.a), b = this.toScreen(this.trail.b), p = this.trail.p;
      const head = {x:a.x + (b.x - a.x) * p, y:a.y + (b.y - a.y) * p};
      const fade = this.trail.fade ? Math.max(0, 1 - (now - this.trail.fade) / 360) : 1;
      const grad = ctx.createLinearGradient(a.x, a.y, head.x, head.y);
      grad.addColorStop(0, withAlpha(agent, 0.35 * fade)); grad.addColorStop(1, withAlpha(agent, fade));
      ctx.save();
      ctx.lineCap = 'round'; ctx.strokeStyle = grad; ctx.lineWidth = Math.max(2.5, 3.5 * zoom);
      ctx.shadowColor = withAlpha(agent, 0.6 * fade); ctx.shadowBlur = 12;
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(head.x, head.y); ctx.stroke();
      ctx.restore();
      live = true;
    }
    // The agent itself: a glowing dot with a slow breathing halo while resting.
    if (this.pos && this.visible > 0.01) {
      const p = this.toScreen(this.pos);
      const r = 6 + Math.min(2, zoom * 1.5);
      const moving = !!(this.trail && !this.trail.fade) || !!this.glide;
      const breathe = REDUCED.matches || moving ? 0 : (Math.sin(now / 520) + 1) / 2;
      ctx.save();
      ctx.globalAlpha = this.visible;
      ctx.fillStyle = withAlpha(agent, 0.18 + 0.1 * breathe);
      ctx.beginPath(); ctx.arc(p.x, p.y, r + 6 + breathe * 4, 0, Math.PI * 2); ctx.fill();
      ctx.shadowColor = agent; ctx.shadowBlur = moving ? 18 : 10;
      ctx.fillStyle = agent; ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, Math.PI * 2); ctx.fill();
      ctx.shadowBlur = 0; ctx.lineWidth = 2; ctx.strokeStyle = '#fff'; ctx.stroke();
      ctx.restore();
      if (!REDUCED.matches) live = true;
    }
    return live;
  },
};

function ring(ctx, c, radius, color, alpha, width) {
  ctx.save(); ctx.strokeStyle = withAlpha(color, alpha); ctx.lineWidth = width;
  ctx.beginPath(); ctx.arc(c.x, c.y, radius, 0, Math.PI * 2); ctx.stroke(); ctx.restore();
}
function withAlpha(color, alpha) {
  const m = color.match(/^#([0-9a-f]{6})$/i);
  if (!m) return color;
  const n = parseInt(m[1], 16);
  return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${Math.max(0, Math.min(1, alpha))})`;
}

/* ------------------------------------------------------------------ runs */
async function sentinelStart(runAll) {
  $('error').textContent = '';
  sentinelBusy(true);
  try {
    const run = await sentinelApi('/runs', {method:'POST', body:{task:$('task').value, requester:'demo user', run_all:runAll}});
    try { localStorage.setItem('sentinel.run', run.run_id); } catch (_) { /* storage unavailable */ }
    await sentinelLoad(run.run_id, true);
  } catch (error) { $('error').textContent = error.message; sentinelBusy(false); }
}

function sentinelBusy(busy) {
  for (const id of ['start', 'runAll', 'task']) $(id).disabled = busy;
  for (const input of $('manualToggles').querySelectorAll('input')) input.disabled = busy;
}

async function sentinelLoad(id, fresh = false) {
  const epoch = ++T.epoch;
  T.stream?.close();
  T.lastStep = 0; T.shown = 0; T.queued = 0; T.playing = false;
  T.planKey = null;
  T.selectedSource = null;
  if (T.downloadUrl) URL.revokeObjectURL(T.downloadUrl);
  T.downloadUrl = null;
  $('planMarkdown').replaceChildren();
  $('downloadPlan').hidden = true;
  $('planStatus').textContent = 'Waiting for the collected answers and synthesis.';
  for (const el of ['log', 'manualAnswers', 'summary']) $(el).replaceChildren();
  const run = await sentinelApi('/runs/' + id);
  run.events = await sentinelApi(`/runs/${id}/events`);
  const pipeline = await sentinelApi('/pipeline?batch=' + encodeURIComponent(run.batch_id));
  if (epoch !== T.epoch) return;
  T.pipeline = pipeline;
  sentinelGraph(pipeline);
  T.run = run;
  $('task').value = run.task;
  // A freshly started run plays from its first event; a reopened run shows its history at once.
  if (!fresh) {
    sentinelApply(run.events.length, true);
    T.queued = run.events.length;
    const last = [...run.events].reverse().find(e => e.target_node && !TERMINAL.includes(run.status));
    if (last) { const node = S.cy.getElementById(last.target_node); if (node.length) Agent.glideTo(node, 0); }
  }
  sentinelRender();
  if (TERMINAL.includes(run.status)) return;
  const stream = new EventSource(`/api/watcher/runs/${id}/stream?after=${run.events.length}`);
  T.stream = stream;
  stream.addEventListener('state', event => {
    if (epoch !== T.epoch) return;
    const next = JSON.parse(event.data);
    if (next.revision < T.run.revision) return;
    T.run = next;
    sentinelRender();
    if (TERMINAL.includes(next.status)) stream.close();
  });
  stream.onerror = () => { if (epoch === T.epoch && !TERMINAL.includes(T.run.status)) $('runMeta').textContent = 'Reconnecting to saved events…'; };
}

/** New state arrived: queue unseen events for playback, refresh non-graph panels. */
function sentinelRender() {
  const run = T.run;
  if (run.events.length > T.queued) { T.queued = run.events.length; sentinelPlay(T.epoch); }
  sentinelPanels();
}

async function sentinelPlay(epoch) {
  if (T.playing) return;
  T.playing = true;
  try {
    let peak = 0;
    while (epoch === T.epoch && T.shown < T.queued) {
      const event = T.run.events[T.shown];
      // Live events trickle in and play at full speed; a large backlog (catching up) is
      // fast-forwarded so the whole burst lands within ~2.6s instead of lagging behind.
      const backlog = T.queued - T.shown;
      peak = backlog <= 1 ? 0 : Math.max(peak, backlog);
      const speed = peak ? Math.min(1, 2600 / (peak * 850)) : 1;
      await sentinelMotion(event, speed);
      if (epoch !== T.epoch) return;
      sentinelApply(T.shown + 1, false);
    }
  } finally { if (epoch === T.epoch) T.playing = false; }
  if (epoch === T.epoch) sentinelPanels();
}

async function sentinelMotion(event, speed) {
  const cy = S.cy;
  const node = event.target_node ? cy.getElementById(event.target_node) : null;
  const edge = event.edge ? cy.getElementById(event.edge) : null;
  if (edge?.length && node?.length) await Agent.travel(edge, node, speed);
  else if (node?.length && ['human_answer', 'persona_answer'].includes(event.action)) {
    Agent.ripple(node, tok('--sn-ok'));
    await pause(REDUCED.matches ? 0 : 260 * speed);
  } else if (node?.length) await Agent.glideTo(node, speed);
  else await pause(REDUCED.matches ? 0 : 160 * speed);
}
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

/** Apply graph classes and timeline rows for the first `count` events. */
function sentinelApply(count, instant) {
  const run = T.run;
  const events = run.events.slice(0, count);
  const outstanding = new Map();
  const answered = new Set();
  const cy = S.cy;
  cy.batch(() => {
    cy.elements().removeClass('waiting consulted plan-owner');
    if (instant) cy.edges().removeClass('traversed');
    for (const event of events) {
      if (event.action === 'ask_human') outstanding.set(event.result.request_id, event);
      if (['human_answer', 'persona_answer'].includes(event.action)) {
        outstanding.delete(event.result.request_id);
        answered.add(event.target_node);
      }
      if (event.action === 'synthesis') {
        for (const owner of event.result.task_owners) cy.getElementById(owner).addClass('plan-owner');
        if (T.planKey !== run.run_id) { T.planKey = run.run_id; sentinelLoadPlan(run.run_id, T.epoch); }
      }
      if (event.action === 'synthesis_started' && T.planKey !== run.run_id)
        $('planStatus').textContent = 'Synthesizing and validating the build plan…';
      if (event.action === 'cancel') outstanding.clear();
      if (event.edge && instant) cy.getElementById(event.edge).addClass('traversed');
    }
    for (const person of answered) cy.getElementById(person).addClass('consulted');
    for (const event of outstanding.values()) cy.getElementById(event.target_node).addClass('waiting');
  });
  // Timeline: append only the rows not yet shown so earlier rows never re-render.
  const log = $('log');
  for (let i = log.children.length; i < events.length; i++) log.append(logRow(events[i], !instant));
  T.shown = count;
  T.lastStep = events.at(-1)?.step || 0;
  if (!instant) followLog();
  if (Tip.target) Tip.show(Tip.target);
  sentinelPanels();
}

const ACTION_KIND = {ask_human:'wait', human_answer:'ok', persona_answer:'ok', who_should_i_ask:'route', escalation_chain:'route',
  synthesis:'plan', synthesis_started:'plan', conclude:'done', cancel:'bad', start:'start'};
function logRow(event, animate) {
  const who = event.actor?.startsWith('persona:') ? personName(event.actor.slice(8))
    : event.actor === 'agent' ? 'Agent' : event.actor === 'system' ? 'System' : event.actor;
  const row = h('li', {class:event.action + (animate ? ' enter' : ''), 'data-step':event.step, 'data-kind':ACTION_KIND[event.action] || 'route'},
    h('i', {'aria-hidden':'true'}),
    h('div', {}, h('small', {}, `Step ${event.step} · ${who}`),
      h('p', {}, event.action === 'conclude' ? 'Investigation complete. Summary below.' : event.result_summary)));
  if (animate) requestAnimationFrame(() => requestAnimationFrame(() => row.classList.remove('enter')));
  return row;
}
function followLog() {
  const box = $('logScroll');
  const last = $('log').lastElementChild;
  if (!last) return;
  box.scrollTo({top:last.offsetTop - box.clientHeight + last.offsetHeight + 24, behavior:REDUCED.matches ? 'auto' : 'smooth'});
}

/** Everything that is not the graph: status, summary, manual forms, buttons. */
function sentinelPanels() {
  const run = T.run;
  if (!run) return;
  const caughtUp = T.shown >= run.events.length;
  const shown = run.events.slice(0, T.shown);
  const outstanding = new Map(); const answered = new Set(); let summary = '';
  for (const event of shown) {
    if (event.action === 'ask_human') outstanding.set(event.result.request_id, event);
    if (['human_answer', 'persona_answer'].includes(event.action)) { outstanding.delete(event.result.request_id); answered.add(event.target_node); }
    if (event.action === 'cancel') outstanding.clear();
    if (event.action === 'conclude') summary = event.result_summary;
  }
  $('logEmpty').hidden = shown.length > 0;
  $('stepCount').textContent = shown.length ? `${shown.length} steps` : '';
  const summaryEl = $('summary');
  if (summary && !summaryEl.childElementCount) {
    summaryEl.replaceChildren(h('h3', {}, 'Incident summary'), h('p', {}, summary));
    summaryEl.classList.add('enter'); requestAnimationFrame(() => requestAnimationFrame(() => summaryEl.classList.remove('enter')));
  } else if (!summary) summaryEl.replaceChildren();

  // Manual answer forms come from committed state, not playback, so a person can answer immediately.
  const all = new Map();
  for (const event of run.events) {
    if (event.action === 'ask_human') all.set(event.result.request_id, event);
    if (['human_answer', 'persona_answer'].includes(event.action)) all.delete(event.result.request_id);
    if (event.action === 'cancel') all.clear();
  }
  const manual = [...all.values()].filter(event => event.result.manual);
  const currentForms = new Set([...$('manualAnswers').querySelectorAll('form')].map(f => f.dataset.request));
  const wantedForms = new Set(manual.map(e => e.result.request_id));
  for (const form of $('manualAnswers').querySelectorAll('form')) if (!wantedForms.has(form.dataset.request)) form.remove();
  for (const event of manual) if (!currentForms.has(event.result.request_id)) sentinelQuestion(event);

  const terminal = TERMINAL.includes(run.status);
  sentinelBusy(!(terminal && caughtUp));
  const status = caughtUp ? run.status : 'running';
  $('runMeta').textContent = run.execution_error || run.projection_error ||
    `${status.replaceAll('_', ' ')} · ${outstanding.size} awaiting answers · ${answered.size} people consulted`;
  const [state, label] = run.execution_error || run.projection_error ? ['bad', 'Needs attention']
    : status === 'done' ? ['done', 'Done'] : status === 'cancelled' ? ['idle', 'Cancelled']
    : outstanding.size ? ['wait', `Waiting on ${outstanding.size} ${outstanding.size === 1 ? 'person' : 'people'}`]
    : ['run', 'Investigating'];
  setPill(state, label);
  if (terminal && caughtUp) { Agent.wantVisible = false; S.cy.nodes('.focus').removeClass('focus'); Agent.kick(); }
}
function setPill(state, label) {
  const pill = $('statusPill');
  pill.dataset.state = state;
  pill.lastElementChild.textContent = label;
}

function sentinelQuestion(event) {
  const name = personName(event.target_node);
  const input = h('textarea', {rows:3, required:true, 'aria-label':'Answer for ' + name, placeholder:'Type the answer ' + name + ' would give…'});
  const submit = h('button', {type:'submit', class:'primary'}, 'Submit answer for ' + name);
  const form = h('form', {'data-request':event.result.request_id, class:'enter'},
    h('header', {}, h('span', {class:'sn-avatar'}, initials(name)), h('div', {}, h('h3', {}, name), h('small', {}, 'Manual mode · waiting for you'))),
    h('p', {}, event.result.question), input, submit);
  form.onsubmit = async e => {
    e.preventDefault(); submit.disabled = true;
    try {
      await sentinelApi(`/pending_requests/${event.result.request_id}/answer`, {method:'POST', body:{person:event.target_node, answer:input.value}});
      // SSE supplies committed events; never fabricate an answer in the UI.
    } catch (error) { $('error').textContent = error.message; submit.disabled = false; }
  };
  $('manualAnswers').append(form);
  requestAnimationFrame(() => requestAnimationFrame(() => form.classList.remove('enter')));
}

async function sentinelReset() {
  try {
    if (T.run && !TERMINAL.includes(T.run.status)) await sentinelApi(`/runs/${T.run.run_id}/reset`, {method:'POST'});
    ++T.epoch; T.stream?.close(); T.run = null; T.lastStep = 0; T.shown = 0; T.queued = 0; T.playing = false;
    try { localStorage.removeItem('sentinel.run'); } catch (_) { /* storage unavailable */ }
    S.cy.elements().removeClass('waiting consulted traversed plan-owner source-highlight');
    Agent.reset(); Tip.hide();
    T.planKey = null; T.selectedSource = null;
    if (T.downloadUrl) URL.revokeObjectURL(T.downloadUrl);
    T.downloadUrl = null;
    $('planMarkdown').replaceChildren(); $('downloadPlan').hidden = true;
    $('planStatus').textContent = 'A cited build plan appears after all answers are collected.';
    for (const id of ['log', 'manualAnswers', 'summary']) $(id).replaceChildren();
    $('logEmpty').hidden = false; $('stepCount').textContent = '';
    $('runMeta').textContent = 'Ready for a new investigation. Previous events remain in Delta.';
    setPill('idle', 'Ready');
    $('error').textContent = ''; sentinelBusy(false);
  } catch (error) { $('error').textContent = error.message; }
}

async function sentinelLoadPlan(runId, epoch) {
  try {
    const result = await sentinelApi(`/runs/${runId}/plan`);
    if (epoch !== T.epoch || T.run?.run_id !== runId) return;
    sentinelMarkdown(result.markdown);
    $('planStatus').textContent = `${result.plan.tasks.length} tasks · owners validated against the ontology · saved to Unity Catalog`;
    const download = $('downloadPlan');
    if (T.downloadUrl) URL.revokeObjectURL(T.downloadUrl);
    T.downloadUrl = URL.createObjectURL(new Blob([result.markdown], {type:'text/markdown;charset=utf-8'}));
    download.href = T.downloadUrl;
    download.download = 'plan.md'; download.hidden = false;
  } catch (error) {
    if (epoch === T.epoch) {
      T.planKey = null;
      $('planStatus').textContent = 'Could not load the stored plan: ' + error.message;
    }
  }
}

function sentinelSource(email) {
  const node = S.cy.getElementById(email);
  if (!node.length || !node.hasClass('person')) return;
  T.selectedSource = email;
  S.cy.nodes().removeClass('source-highlight');
  node.addClass('source-highlight');
  $('cy').scrollIntoView({behavior:REDUCED.matches ? 'auto' : 'smooth', block:'center'});
  setTimeout(() => Tip.show(node), REDUCED.matches ? 0 : 450);
}

function sentinelMarkdown(markdown) {
  // Render the canonical server Markdown subset into DOM nodes, never raw HTML.
  const root = $('planMarkdown'); root.replaceChildren();
  const plain = value => value.replace(/\\([\\`*_{}\[\]<>#!|])/g, '$1');
  function inline(parent, value) {
    const pattern = /\[([^\]]+)\]\(#person=([^\s)]+)\)|\*\*([^*]+)\*\*/g;
    let last = 0, match;
    while ((match = pattern.exec(value))) {
      parent.append(document.createTextNode(plain(value.slice(last, match.index))));
      if (match[1]) {
        const email = match[2];
        const link = h('a', {href:'#person=' + encodeURIComponent(email), class:'plan-source', 'data-person':email}, plain(match[1]));
        link.onclick = event => { event.preventDefault(); sentinelSource(email); };
        parent.append(link);
      } else parent.append(h('strong', {}, plain(match[3])));
      last = pattern.lastIndex;
    }
    parent.append(document.createTextNode(plain(value.slice(last))));
  }
  let list = null;
  for (const line of markdown.split('\n')) {
    if (!line.trim()) { list = null; continue; }
    const heading = line.match(/^(#{1,3}) (.*)$/);
    let element;
    if (heading) {
      list = null; element = h('h' + heading[1].length); inline(element, heading[2]); root.append(element);
    } else if (line.startsWith('- ')) {
      if (!list) { list = h('ul'); root.append(list); }
      element = h('li'); inline(element, line.slice(2)); list.append(element);
    } else {
      list = null; element = h('p'); inline(element, line); root.append(element);
    }
  }
}
