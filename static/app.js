/* Agent Flow Debugger - front end (no build step).
 *
 * Sections
 *   0. tiny helpers (api, h)
 *   1. state + boot
 *   2. graph: layout, semantic zoom, live highlighting, markers, action nodes
 *   3. run tab: composer, log, detail/decision cards, evidence view, replay, SSE
 *   4. inbox (human in the loop)
 *   5. review tab: flags, proposals (accept/edit/reject), mark wrong, rerun, compare
 *   6. editors: rule editor + ontology editor
 *   7. history: versions + rollback, audit log, test cases, instructions
 *   8. benchmark
 *
 * Fidelity labels are shown wherever content appears:
 *   observed (tool calls + returned data), reported (the agent's own rationale),
 *   inferred (our narrator sentences, flags and proposals).
 */
"use strict";

// ------------------------------------------------------------ 0. helpers
async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (_) { /* keep statusText */ }
    throw new Error(msg);
  }
  return res.json();
}

/** h("div", {class: "x", onclick: fn}, child, "text", ...) -> Element */
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const $ = (id) => document.getElementById(id);
const chip = (cls, text) => h("span", { class: `chip ${cls}` }, text || cls);
const pretty = (o) => JSON.stringify(o, null, 2);
const shortTime = (iso) => (iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "");
function toast(msg, bad = false) {
  const t = h("div", { style: `position:fixed;right:20px;bottom:20px;z-index:99;padding:10px 14px;border-radius:8px;
    background:${bad ? "var(--bad)" : "var(--ink)"};color:var(--panel);box-shadow:var(--shadow)` }, msg);
  document.body.append(t);
  setTimeout(() => t.remove(), 3200);
}
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

// ------------------------------------------------------------ 1. state + boot
const S = {
  meta: null,
  me: null,
  runs: [],
  run: null, events: [], flags: [], proposals: [],
  snapshot: null,          // ontology snapshot the selected run ran against
  step: null,              // null = live (latest); a number = replay position (seq)
  selectedSeq: null,
  evidence: null,          // {nodes:Set, edges:Set} when a claim is selected
  es: null,                // EventSource for the live run
  cy: null,
  playing: null,
};

async function boot() {
  const mode = await api("/api/mode");
  if (mode.workflow === "sentinel") return sentinelBoot();
  if (mode.workflow === "watcher") return watcherBoot();
  S.meta = await api("/api/meta");
  // "You are": flat access, used for attribution only.
  const me = $("me");
  for (const p of S.meta.people) me.append(h("option", { value: p.id }, `${p.label} - ${p.title}`));
  try { S.me = localStorage.getItem("afd.me") || "p_leo"; } catch (_) { S.me = "p_leo"; }
  me.value = S.me;
  me.onchange = () => { S.me = me.value; try { localStorage.setItem("afd.me", S.me); } catch (_) {} };

  const m = S.meta.modes;
  $("modes").replaceChildren(
    h("span", {}, `agent host: ${m.agent_host}`), h("span", {}, `narrator: ${m.narrator}`),
    h("span", {}, `reviewer: ${m.reviewer}`), h("span", {}, `GitHub: ${m.github}`),
    h("span", {}, `Gmail: ${m.gmail}`), h("span", {}, `Delta mirror: ${m.delta ? "on" : "off"}`),
    h("span", {}, `Genie: ${m.genie ? "on" : "off"}`),
    h("span", {}, m.auto_reply_s >= 0 ? `simulated teammates reply after ${m.auto_reply_s}s` : "humans reply in the inbox"),
    h("span", {}, `today: ${S.meta.today}`));

  document.querySelectorAll("#tabs button").forEach((b) => (b.onclick = () => showTab(b.dataset.tab)));
  initGraph();
  bindRunTab();
  bindReviewTab();
  bindEditors();
  $("benchRun").onclick = runBenchmark;
  await refreshRuns();
  if (S.runs.length) await loadRun(S.runs[0].run_id);
  else { S.snapshot = await api("/api/ontology"); buildGraph(S.snapshot); }
  setInterval(pollInbox, 1500);
  pollInbox();
  // Deep links: /#review, /#editors, ... open that tab.
  const tab = location.hash.slice(1);
  if (["review", "editors", "history", "benchmark"].includes(tab)) showTab(tab);
}

function showTab(name) {
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.id === `tab-${name}`));
  if (name === "run" && S.cy) setTimeout(() => S.cy.resize(), 0);
  if (name === "review") renderReview();
  if (name === "editors") renderEditors();
  if (name === "history") renderHistory();
  if (name === "benchmark") renderBenchmark();
}

// ------------------------------------------------------------ 2. graph
// Fixed lanes left -> right follow how the agent usually walks:
// task -> service -> document / repo area -> person -> team.
// Positions never change while action nodes expand (spec: "positions stay fixed").
const LANES = { task: 0, ticket: 0, service: 1, document: 2, repo_area: 2, person: 3, team: 4 };
const LANE_W = 240, ROW_H = 74;
const MARKERS = { github: "⑂", csv: "▦", web: "◍", message: "✉", ask: "?", genie: "◆" };
const EXPAND_ZOOM = 1.35; // zoom level at which markers expand into action nodes

function initGraph() {
  S.cy = cytoscape({
    container: $("cy"),
    wheelSensitivity: 0.25,
    minZoom: 0.3, maxZoom: 3,
    style: graphStyle(),
  });
  S.cy.on("zoom", () => { updateZoomHint(); renderGraphState(); });
  S.cy.on("tap", "node", (e) => {
    const n = e.target;
    if (n.hasClass("action")) selectEvent(n.data("seq"), false);
    else showNodeDetail(n.id());
  });
  $("fit").onclick = () => S.cy.fit(S.cy.nodes(":not(.action)"), 30);
  const types = ["task", "service", "document", "repo_area", "person", "team", "ticket"];
  $("legend").replaceChildren(...types.map((t) => h("span", {}, h("i", { style: `background:${cssVar("--" + t)}` }), t)),
    h("span", {}, "· dotted edge = stale (>180 days) · red dashed = flagged"));
}

function graphStyle() {
  const ink = cssVar("--ink"), muted = cssVar("--muted"), line = cssVar("--line");
  const types = ["person", "team", "service", "repo_area", "ticket", "task", "document"];
  return [
    { selector: "node", style: {
      "background-color": cssVar("--action"), label: "data(display)", "font-size": 10, color: ink,
      "text-valign": "bottom", "text-margin-y": 4, "text-wrap": "wrap", "text-max-width": 140,
      width: 24, height: 24, "border-width": 0, "border-color": cssVar("--accent"),
      "transition-property": "border-width, opacity, background-color", "transition-duration": "180ms" } },
    ...types.map((t) => ({ selector: `node.${t}`, style: { "background-color": cssVar("--" + t) } })),
    { selector: "node.person", style: { shape: "ellipse" } },
    { selector: "node.document", style: { shape: "round-rectangle", width: 22, height: 26 } },
    { selector: "node.service", style: { shape: "round-hexagon", width: 28, height: 28 } },
    { selector: "node.task", style: { shape: "star", width: 30, height: 30 } },
    { selector: "node.dim", style: { opacity: 0.4 } },
    { selector: "node.visited", style: { "border-width": 3 } },
    { selector: "node.warn", style: { "border-color": cssVar("--warn"), "border-width": 4 } },
    { selector: "node.active", style: { "border-width": 6, "underlay-color": cssVar("--accent"),
      "underlay-padding": 9, "underlay-opacity": 0.22, "underlay-shape": "ellipse" } },
    { selector: "node.evidence", style: { "underlay-color": cssVar("--good"), "underlay-padding": 10,
      "underlay-opacity": 0.35, "border-color": cssVar("--good"), "border-width": 4 } },
    { selector: "node.action", style: { shape: "round-rectangle", width: 14, height: 14, "font-size": 8,
      color: muted, "background-color": cssVar("--action"), "text-valign": "center", "text-halign": "right",
      "text-margin-x": 3, "text-margin-y": 0 } },
    { selector: "node.action.blocked", style: { "background-color": cssVar("--bad") } },
    { selector: "edge", style: {
      width: 1.4, "line-color": line, "curve-style": "bezier", "target-arrow-shape": "triangle",
      "target-arrow-color": line, "arrow-scale": 0.7, label: "data(type)", "font-size": 7, color: muted,
      "min-zoomed-font-size": 9, "text-rotation": "autorotate", "text-background-color": cssVar("--panel"),
      "text-background-opacity": 1, "text-background-padding": 1 } },
    { selector: "edge.stale", style: { "line-style": "dotted", width: 2 } },
    { selector: "edge.dim", style: { opacity: 0.35 } },
    { selector: "edge.trav", style: { "line-color": cssVar("--accent"), "target-arrow-color": cssVar("--accent"),
      width: 3, opacity: 1 } },
    { selector: "edge.flagged", style: { "line-color": cssVar("--bad"), "target-arrow-color": cssVar("--bad"),
      "line-style": "dashed", width: 3.5 } },
    { selector: "edge.evidence", style: { "line-color": cssVar("--good"), "target-arrow-color": cssVar("--good"), width: 4 } },
    { selector: "edge.action-link", style: { width: 1, "line-style": "dashed", "target-arrow-shape": "none",
      label: "", opacity: 0.7 } },
  ];
}

function ageDays(iso) {
  return Math.round((new Date(S.meta.today) - new Date(iso)) / 86400000);
}

function buildGraph(snapshot) {
  const cy = S.cy;
  cy.elements().remove();
  const lanes = {};
  const order = ["task", "ticket", "service", "document", "repo_area", "person", "team"];
  const nodes = [...snapshot.nodes].sort((a, b) => order.indexOf(a.type) - order.indexOf(b.type) || a.label.localeCompare(b.label));
  for (const n of nodes) (lanes[LANES[n.type]] ||= []).push(n);
  const els = [];
  for (const [lane, list] of Object.entries(lanes)) {
    list.forEach((n, i) => {
      els.push({ group: "nodes", data: { id: n.id, label: n.label, display: n.label, type: n.type },
                 classes: n.type, position: { x: lane * LANE_W, y: (i - (list.length - 1) / 2) * ROW_H } });
    });
  }
  for (const e of snapshot.edges) {
    els.push({ group: "edges", data: { id: e.id, source: e.src, target: e.dst, type: e.type.replace("_", " ") },
               classes: ageDays(e.last_verified) > 180 ? "stale" : "" });
  }
  cy.add(els);
  cy.nodes().lock(); // fixed positions: expansion never moves the ontology
  cy.fit(cy.nodes(), 30);
  S.graphVersion = snapshot.version;
  updateZoomHint();
}

function updateZoomHint() {
  $("zoomHint").textContent = S.cy.zoom() >= EXPAND_ZOOM ? "zoomed in: actions expanded" : "zoom in to expand actions";
}

/** Events visible at the current replay position. */
function visibleEvents() {
  return S.step === null ? S.events : S.events.filter((e) => e.seq <= S.step);
}

/** Map each tool action to the ontology node that "triggered" it. */
function actionAnchors(events) {
  const out = [];
  let last = null;
  for (const e of events) {
    const onNode = e.focus_node && S.cy.getElementById(e.focus_node).nonempty() ? e.focus_node : null;
    if (onNode) last = onNode;
    const spec = S.meta.tools[e.tool];
    const marker = e.action_type === "blocked" ? "blocked" : spec && spec.marker;
    if (marker && (e.action_type !== "observe" || e.tool !== "ask_human")) {
      const anchor = onNode || last;
      if (anchor) out.push({ anchor, marker, ev: e });
    }
  }
  return out;
}

function renderGraphState() {
  const cy = S.cy;
  if (!cy || !S.meta) return;
  const evs = visibleEvents();
  cy.batch(() => {
    cy.elements(".action, .action-link").remove();
    cy.elements().removeClass("visited active warn trav flagged evidence dim");
    if (!S.run) return;
    const visited = new Set(), trav = new Set(), warned = new Set();
    let active = null;
    for (const e of evs) {
      e.node_ids.forEach((n) => visited.add(n));
      if (["traverse", "decide", "ask_human", "act", "observe"].includes(e.action_type)) e.edge_ids.forEach((x) => trav.add(x));
      if (e.warnings && e.warnings.length) e.node_ids.slice(0, 1).concat(e.edge_ids.map((x) => cy.getElementById(x).data("source"))).forEach((n) => n && warned.add(n));
      if (e.focus_node && cy.getElementById(e.focus_node).nonempty()) active = e.focus_node;
    }
    const lastSeq = evs.length ? evs[evs.length - 1].seq : 0;
    const flaggedEdges = new Set(S.flags.filter((f) => f.seq <= lastSeq).flatMap((f) => f.edge_ids));
    cy.nodes().forEach((n) => { if (!visited.has(n.id())) n.addClass("dim"); else n.addClass("visited"); });
    cy.edges().forEach((ed) => { if (!trav.has(ed.id())) ed.addClass("dim"); else ed.addClass("trav"); });
    warned.forEach((n) => cy.getElementById(n).addClass("warn"));
    flaggedEdges.forEach((x) => cy.getElementById(x).addClass("flagged"));
    if (active) cy.getElementById(active).addClass("active");

    // Markers (zoomed out) and action nodes (zoomed in / active node while live).
    const anchors = actionAnchors(evs);
    const byNode = {};
    for (const a of anchors) (byNode[a.anchor] ||= []).push(a);
    const live = S.step === null && S.run.status === "running";
    const expandAll = cy.zoom() >= EXPAND_ZOOM;
    cy.nodes(":not(.action)").forEach((n) => {
      const list = byNode[n.id()] || [];
      const counts = {};
      list.forEach((a) => (counts[a.marker] = (counts[a.marker] || 0) + 1));
      const marks = Object.entries(counts).map(([m, c]) => `${m === "blocked" ? "⛔" : MARKERS[m] || "•"}${c > 1 ? c : ""}`).join(" ");
      n.data("display", marks ? `${n.data("label")}\n${marks}` : n.data("label"));
      const expand = expandAll || (live && n.id() === active);
      if (!expand || !list.length) return;
      const p = n.position();
      list.forEach((a, k) => {
        const id = `act-${a.ev.seq}`;
        cy.add({ group: "nodes", data: { id, seq: a.ev.seq, display: `#${a.ev.seq} ${shortTool(a.ev)}` },
                 classes: `action ${a.marker === "blocked" ? "blocked" : ""}`,
                 position: { x: p.x + 34 + (k % 3) * 62, y: p.y + 30 + Math.floor(k / 3) * 22 } });
        cy.add({ group: "edges", data: { id: `${id}-l`, source: n.id(), target: id }, classes: "action-link" });
      });
    });
    if (S.evidence) {
      S.evidence.nodes.forEach((n) => cy.getElementById(n).addClass("evidence").removeClass("dim"));
      S.evidence.edges.forEach((x) => cy.getElementById(x).addClass("evidence").removeClass("dim"));
    }
  });
}

function shortTool(e) {
  const names = { github_create_branch: "branch", github_open_pr: "PR", edit_file: "write", read_file: "read",
                  analyze_csv: "csv", web_search: "web", message_human: "message", ask_human: "ask", genie_query: "genie" };
  return (e.action_type === "blocked" ? "blocked " : "") + (names[e.tool] || e.tool);
}

// ------------------------------------------------------------ 3. run tab
function bindRunTab() {
  $("start").onclick = startRun;
  $("runPicker").onchange = (e) => loadRun(e.target.value);
  $("allEvents").onchange = renderLog;
  $("scrub").oninput = (e) => setStep(Number(e.target.value));
  $("prev").onclick = () => setStep(Math.max(1, (S.step ?? lastSeq()) - 1));
  $("next").onclick = () => setStep(Math.min(lastSeq(), (S.step ?? lastSeq()) + 1));
  $("live").onclick = () => setStep(null);
  $("play").onclick = togglePlay;
}
const lastSeq = () => (S.events.length ? S.events[S.events.length - 1].seq : 0);

async function refreshRuns() {
  S.runs = await api("/api/runs");
  const pick = $("runPicker");
  pick.replaceChildren(...S.runs.map((r) => h("option", { value: r.run_id },
    `${shortTime(r.created_at)} · ${r.status} · ${r.task.slice(0, 38)}${r.rerun_of ? " (rerun)" : ""}`)));
  if (S.run) pick.value = S.run.run_id;
}

async function startRun() {
  const task = $("task").value.trim();
  if (!task) return;
  $("start").disabled = true;
  try {
    const run = await api("/api/runs", { method: "POST", body: { task, requester: S.me } });
    await refreshRuns();
    await loadRun(run.run_id);
  } catch (e) { toast(e.message, true); }
  $("start").disabled = false;
}

async function loadRun(runId) {
  if (S.es) { S.es.close(); S.es = null; }
  stopPlay();
  const data = await api(`/api/runs/${runId}`);
  S.run = data.run; S.events = data.events; S.flags = data.flags; S.proposals = data.proposals;
  S.step = null; S.selectedSeq = null; S.evidence = null;
  if (!S.snapshot || S.graphVersion !== S.run.ontology_version) {
    S.snapshot = await api(`/api/ontology?version=${S.run.ontology_version}`);
    buildGraph(S.snapshot);
  }
  $("runPicker").value = runId;
  renderRunAll();
  if (S.run.status === "running") connectStream(runId);
}

/** Server-sent events: append events as they arrive; nodes light up live. */
function connectStream(runId) {
  const es = new EventSource(`/api/runs/${runId}/stream?after=${lastSeq()}`);
  S.es = es;
  es.addEventListener("ev", (m) => {
    const ev = JSON.parse(m.data);
    if (ev.run_id !== S.run.run_id || ev.seq <= lastSeq()) return;
    S.events.push(ev);
    renderRunAll();
  });
  es.addEventListener("state", (m) => {
    const st = JSON.parse(m.data);
    if (st.run.run_id === S.run.run_id) { S.run = st.run; renderRunMeta(); }
  });
  es.addEventListener("done", async (m) => {
    const d = JSON.parse(m.data);
    S.flags = d.flags; S.proposals = d.proposals;
    es.close(); S.es = null;
    S.run = (await api(`/api/runs/${runId}`)).run;
    await refreshRuns();
    renderRunAll();
    if (S.flags.length) toast(`Run finished with ${S.flags.length} flag(s). See Review.`);
  });
  es.onerror = () => { /* the browser retries automatically; 'done' closes it */ };
}

function renderRunAll() {
  renderRunMeta();
  renderLog();
  renderGraphState();
  renderAnswer();
  renderReplayControls();
  if (S.selectedSeq) renderEventDetail(S.selectedSeq);
}

function renderRunMeta() {
  const r = S.run;
  if (!r) return;
  const flagsText = S.flags.length ? ` · ${S.flags.length} flag(s)` : "";
  $("runMeta").replaceChildren(h("span", {},
    `${r.run_id} · `, chip(r.status === "completed" ? "good" : r.status === "running" ? "stage" : "bad", r.status),
    ` · ontology v${r.ontology_version} · rules v${r.config_version} · host ${r.host}${flagsText}`,
    r.rerun_of ? ` · rerun of ${r.rerun_of}` : null,
    r.mlflow_trace_id ? ` · MLflow trace ${r.mlflow_trace_id}` : null));
}

function renderLog() {
  const all = $("allEvents").checked;
  const flagged = new Set(S.flags.map((f) => f.seq));
  const items = S.events.filter((e) => all || e.milestone).map((e) => {
    const future = S.step !== null && e.seq > S.step;
    const li = h("li", {
      class: [S.selectedSeq === e.seq ? "sel" : "", future ? "future" : "", e.action_type === "blocked" ? "blocked" : "",
              e.warnings.length ? "warned" : ""].join(" "),
      onclick: () => selectEvent(e.seq, true),
    },
      h("span", { class: "seq" }, e.seq),
      h("div", {},
        h("div", {}, e.plain_summary || `${e.action_type}: ${e.tool}`),
        e.warnings.length ? h("div", { class: "warn-text" }, "⚠ ", e.warnings[0]) : null,
        h("div", { class: "meta" }, chip("stage", e.stage), e.plain_summary ? chip("inferred", `narrated · ${e.narrator}`) : chip("observed"),
          flagged.has(e.seq) ? chip("bad", "flagged") : null,
          e.status !== "ok" ? chip(e.status === "waiting" ? "warn" : "bad", e.status) : null)));
    return li;
  });
  const log = $("log");
  const stick = log.scrollTop + log.clientHeight >= log.scrollHeight - 30;
  log.replaceChildren(...items);
  if (stick && S.step === null) log.scrollTop = log.scrollHeight;
}

function selectEvent(seq, moveReplay) {
  S.selectedSeq = seq;
  if (moveReplay && S.run && S.run.status !== "running") S.step = seq;
  renderLog();
  renderGraphState();
  renderReplayControls();
  renderEventDetail(seq);
}

/** The decision card / raw event inspector with fidelity labels. */
function renderEventDetail(seq) {
  const e = S.events.find((x) => x.seq === seq);
  if (!e) return;
  const label = (id) => (S.cy.getElementById(id).data("label") || id);
  const parts = [
    h("div", { class: "row between" },
      h("h3", {}, `#${e.seq} · ${e.stage} · ${e.tool || e.action_type}`),
      h("div", { class: "row" }, chip("stage", e.action_type), chip(e.status === "ok" ? "good" : "warn", e.status), chip("stage", `${e.latency_ms} ms`))),
  ];
  if (e.plain_summary) parts.push(h("p", {}, e.plain_summary, " ", chip("inferred", `narrator: ${e.narrator}`)));
  if (e.decision) {
    parts.push(h("div", { class: "decision" },
      h("h3", {}, "Decision ", chip("reported")),
      ...(e.decision.options.length ? e.decision.options : [e.decision.chosen]).map((o) =>
        h("div", { class: `opt ${o === e.decision.chosen ? "chosen" : ""}` }, o === e.decision.chosen ? "✓ " : "", o)),
      h("p", { class: "small" }, h("b", {}, "Rationale: "), e.decision.rationale)));
  }
  if (e.warnings.length) parts.push(h("div", { class: "warn-text" }, ...e.warnings.map((w) => h("div", {}, "⚠ ", w))));
  parts.push(h("dl", { class: "kv" },
    h("dt", {}, "actor"), h("dd", {}, e.actor),
    h("dt", {}, "nodes"), h("dd", {}, e.node_ids.map(label).join(", ") || "-"),
    h("dt", {}, "edges"), h("dd", {}, e.edge_ids.join(", ") || "-"),
    h("dt", {}, "evidence"), h("dd", {}, e.evidence_refs.join(", ") || "-"),
    h("dt", {}, "external ids"), h("dd", {}, Object.entries(e.external_ids || {}).map(([k, v]) => `${k}: ${v}`).join(" · ") || "-"),
    h("dt", {}, "versions"), h("dd", {}, `ontology v${e.ontology_version} · rules v${e.config_version}`),
    h("dt", {}, "time"), h("dd", {}, e.timestamp)));
  parts.push(h("h3", { style: "margin-top:10px" }, "Input ", chip("observed")), h("pre", {}, pretty(e.input)));
  parts.push(h("h3", { style: "margin-top:10px" }, "Result ", chip(e.fidelity.result)), h("pre", {}, pretty(e.result)));
  $("detail").replaceChildren(...parts);
}

function showNodeDetail(nodeId) {
  const n = S.snapshot.nodes.find((x) => x.id === nodeId);
  if (!n) return;
  const edges = S.snapshot.edges.filter((e) => e.src === nodeId || e.dst === nodeId);
  const lbl = (id) => (S.snapshot.nodes.find((x) => x.id === id) || { label: id }).label;
  const touching = S.events.filter((e) => e.node_ids.includes(nodeId));
  $("detail").replaceChildren(
    h("h3", {}, `${n.label} `, chip("stage", n.type), " ", chip("observed", `ontology v${S.snapshot.version}`)),
    h("pre", {}, pretty(n.props)),
    h("h3", { style: "margin-top:10px" }, "Edges (provenance)"),
    h("table", {}, h("tr", {}, ...["edge", "source", "conf.", "verified"].map((t) => h("th", {}, t))),
      ...edges.map((e) => h("tr", { class: ageDays(e.last_verified) > 180 ? "stale" : "" },
        h("td", {}, `${lbl(e.src)} ${e.type} ${lbl(e.dst)}`), h("td", {}, e.source), h("td", {}, e.confidence),
        h("td", {}, `${e.last_verified} (${ageDays(e.last_verified)}d)`)))),
    h("h3", { style: "margin-top:10px" }, "Steps that touched it"),
    h("div", { class: "row" }, ...(touching.length ? touching.map((e) => h("button", { class: "ghost small", onclick: () => selectEvent(e.seq, true) }, `#${e.seq} ${e.tool}`)) : [h("span", { class: "muted" }, "none")])));
}

/** Evidence view: click a claim -> highlight the nodes/edges/events behind it. */
function renderAnswer() {
  const conclusion = [...S.events].reverse().find((e) => e.action_type === "conclude");
  $("answerCard").hidden = !conclusion;
  if (!conclusion) return;
  $("answer").textContent = conclusion.result.answer;
  $("claims").replaceChildren(...(conclusion.result.claims || []).map((c, i) => h("li", {
    class: S.evidence && S.evidence.i === i ? "sel" : "",
    onclick: () => toggleEvidence(c, i),
  }, c.text, " ", c.evidence_refs.length ? chip("observed", `${c.evidence_refs.length} evidence`) : chip("bad", "no evidence"))));
}

function toggleEvidence(claim, i) {
  if (S.evidence && S.evidence.i === i) { S.evidence = null; renderAnswer(); renderGraphState(); return; }
  const nodes = new Set(), edges = new Set(), evs = [];
  for (const ref of claim.evidence_refs) {
    const ev = S.events.find((e) => e.event_id === ref);
    if (ev) { evs.push(ev); ev.node_ids.forEach((n) => nodes.add(n)); ev.edge_ids.forEach((x) => edges.add(x)); }
    else if (S.snapshot.nodes.some((n) => n.id === ref)) nodes.add(ref);
    else if (S.snapshot.edges.some((x) => x.id === ref)) {
      edges.add(ref);
      const ed = S.snapshot.edges.find((x) => x.id === ref); nodes.add(ed.src); nodes.add(ed.dst);
    }
  }
  S.evidence = { i, nodes, edges };
  renderAnswer();
  renderGraphState();
  $("detail").replaceChildren(h("h3", {}, "Evidence for: ", h("i", {}, claim.text)),
    h("p", { class: "small muted" }, "Highlighted in green on the graph."),
    ...evs.map((e) => h("div", { class: "row" }, h("button", { class: "ghost small", onclick: () => selectEvent(e.seq, true) }, `#${e.seq}`),
      h("span", {}, e.plain_summary || e.tool), chip("observed"))),
    ...[...edges].map((x) => { const ed = S.snapshot.edges.find((y) => y.id === x);
      return ed ? h("div", { class: "small" }, `edge ${x}: source "${ed.source}", confidence ${ed.confidence}, verified ${ed.last_verified}`) : null; }));
}

function renderReplayControls() {
  const max = lastSeq();
  const scrub = $("scrub");
  scrub.max = String(max);
  scrub.min = max ? "1" : "0";
  scrub.value = String(S.step ?? max);
  const live = S.step === null;
  $("live").disabled = live;
  const ev = S.events.find((e) => e.seq === (S.step ?? max));
  $("stepLabel").textContent = max ? `${live ? "live" : "replay"} · step ${S.step ?? max} of ${max}${ev && ev.plain_summary ? " · " + ev.plain_summary : ""}` : "";
}

function setStep(seq) {
  S.step = seq === null || seq >= lastSeq() ? (S.run && S.run.status === "running" ? null : seq) : seq;
  if (seq === null) S.step = null;
  S.selectedSeq = S.step;
  renderLog(); renderGraphState(); renderReplayControls();
  if (S.step) renderEventDetail(S.step);
}

function togglePlay() {
  if (S.playing) return stopPlay();
  let seq = 1;
  $("play").textContent = "■ stop";
  S.playing = setInterval(() => {
    if (seq > lastSeq()) return stopPlay();
    // step through milestones only, like the plain-language log
    const ev = S.events.find((e) => e.seq === seq);
    if (ev && ev.milestone) setStep(seq);
    seq++;
  }, 280);
}
function stopPlay() { if (S.playing) clearInterval(S.playing); S.playing = null; $("play").textContent = "▶ replay"; }

// ------------------------------------------------------------ 4. inbox
async function pollInbox() {
  let qs = [];
  try { qs = await api("/api/questions"); } catch (_) { return; }
  const box = $("inbox");
  const typing = box.contains(document.activeElement);
  if (typing && box.dataset.ids === qs.map((q) => q.question_id).join()) return; // don't clobber a reply being typed
  box.dataset.ids = qs.map((q) => q.question_id).join();
  box.replaceChildren(...qs.map((q) => {
    const ta = h("textarea", { rows: 2, placeholder: `Reply as ${q.to_label}…` });
    const send = async () => {
      if (!ta.value.trim()) return;
      try { await api(`/api/questions/${q.question_id}/reply`, { method: "POST", body: { text: ta.value, author: S.me } }); toast("Reply sent; the agent continues."); pollInbox(); }
      catch (e) { toast(e.message, true); }
    };
    return h("div", { class: "card question" },
      h("div", { class: "row between" }, h("span", { class: "who" }, `Question for ${q.to_label}`), chip("warn", "agent is waiting")),
      h("p", {}, q.text),
      q.email ? h("div", { class: "email" }, `✉ mirrored to ${q.email.to} via Gmail (${q.email.mode})`) : null,
      ta,
      h("div", { class: "row" }, h("button", { class: "primary", onclick: send }, `Reply as ${q.to_label}`),
        h("span", { class: "small muted" }, `recorded as typed by ${S.me}`)));
  }));
}

// ------------------------------------------------------------ 5. review tab
function bindReviewTab() {
  $("reviewRun").onchange = (e) => loadRun(e.target.value).then(renderReview);
  $("markWrong").onclick = markWrong;
  $("rerun").onclick = rerun;
  $("compareBtn").onclick = doCompare;
  $("reviewAgain").onclick = async () => { await api(`/api/runs/${S.run.run_id}/review`, { method: "POST" }); await reloadRunData(); renderReview(); };
}

async function reloadRunData() {
  const d = await api(`/api/runs/${S.run.run_id}`);
  S.run = d.run; S.flags = d.flags; S.proposals = d.proposals; S.events = d.events;
}

async function renderReview() {
  await refreshRuns();
  const opts = S.runs.map((r) => h("option", { value: r.run_id }, `${shortTime(r.created_at)} · ${r.status} · ${r.task.slice(0, 40)}${r.marked_wrong ? " · marked wrong" : ""}`));
  $("reviewRun").replaceChildren(...opts);
  $("compareWith").replaceChildren(...S.runs.map((r) => h("option", { value: r.run_id }, `${shortTime(r.created_at)} · ${r.task.slice(0, 30)}`)));
  if (!S.run) return;
  $("reviewRun").value = S.run.run_id;
  const other = S.runs.find((r) => r.rerun_of === S.run.run_id) || S.runs.find((r) => r.run_id === S.run.rerun_of);
  if (other) $("compareWith").value = other.run_id;
  $("reviewMeta").textContent = `${S.run.task} · outcome: ${S.run.outcome || "-"} · ${S.run.answer || ""}`;
  $("flags").replaceChildren(...(S.flags.length ? S.flags : [null]).map((f) => f ? h("li", { class: f.severity },
    h("div", { class: "row between" }, h("b", {}, f.title), h("button", { class: "ghost small", onclick: () => { showTab("run"); selectEvent(f.seq, true); } }, `step #${f.seq}`)),
    h("div", { class: "small" }, f.detail), h("div", { class: "row" }, chip("inferred"), chip("stage", f.type))) :
    h("li", { class: "low" }, "No flags on this run.")));
  if (S.run.divergence) renderDivergence(S.run.divergence);
  else $("divergence").replaceChildren();
  renderProposals();
}

function renderProposals() {
  const list = S.proposals;
  $("proposals").replaceChildren(...(list.length ? list : [null]).map((p) => {
    if (!p) return h("p", { class: "muted" }, "No proposals. Proposals appear on flagged runs or when you mark a run wrong.");
    const editor = h("textarea", { rows: 8, hidden: true }, pretty(p.ops));
    const pending = p.status === "pending";
    const act = async (decision, ops) => {
      try {
        await api(`/api/proposals/${p.proposal_id}/${decision}`, { method: "POST", body: { author: S.me, ops } });
        toast(decision === "accept" ? "Applied as a new version." : "Rejected.");
        await reloadRunData(); renderReview(); refreshMeta();
      } catch (e) { toast(e.message, true); }
    };
    return h("div", { class: `proposal ${p.status}` },
      h("div", { class: "row between" }, h("b", {}, p.title),
        h("div", { class: "row" }, chip("inferred", "AI-inferred"), chip("stage", p.target), chip("stage", `by ${p.proposer}`),
          chip(p.status.startsWith("accepted") ? "good" : p.status === "rejected" ? "bad" : "warn", p.status))),
      h("p", { class: "small" }, p.rationale, " ",
        h("a", { href: "#", onclick: (ev) => { ev.preventDefault(); showTab("run"); selectEvent(p.seq, true); } }, `(caused at step #${p.seq})`)),
      h("div", { class: "diff" },
        h("div", {}, h("h5", {}, "before"), h("pre", { class: "before" }, pretty(p.before))),
        h("div", {}, h("h5", {}, "after"), h("pre", { class: "after" }, pretty(p.after)))),
      h("p", { class: "small muted" }, p.limitations),
      editor,
      pending ? h("div", { class: "row" },
        h("button", { class: "primary", onclick: () => act("accept") }, "Accept"),
        h("button", { onclick: () => {
          if (editor.hidden) { editor.hidden = false; return; }
          try { act("accept", JSON.parse(editor.value)); } catch (e) { toast("ops must be valid JSON", true); }
        } }, "Edit → accept"),
        h("button", { class: "danger", onclick: () => act("reject") }, "Reject")) :
        h("div", { class: "small muted" }, `${p.status} by ${p.decided_by || "-"}${p.applied_version ? " → " + Object.entries(p.applied_version).map(([k, v]) => `${k} v${v}`).join(", ") : ""}`));
  }));
}

function renderDivergence(div) {
  $("divergence").replaceChildren(h("p", {}, h("b", {}, "Diverged at step "),
    h("a", { href: "#", onclick: (e) => { e.preventDefault(); showTab("run"); selectEvent(div.seq, true); } }, `#${div.seq}`),
    ": ", div.reason, " ", chip("inferred")), h("p", { class: "small muted" }, "Saved as a test case."));
}

async function markWrong() {
  const text = $("expected").value.trim();
  if (!text || !S.run) return toast("Say what the right outcome was.", true);
  try {
    const res = await api(`/api/runs/${S.run.run_id}/mark`, { method: "POST", body: { expected_outcome: text, author: S.me } });
    renderDivergence(res.divergence);
    await reloadRunData(); renderReview();
  } catch (e) { toast(e.message, true); }
}

async function rerun() {
  if (!S.run) return;
  const r = await api(`/api/runs/${S.run.run_id}/rerun`, { method: "POST", body: { author: S.me } });
  await refreshRuns();
  showTab("run");
  await loadRun(r.run_id);
}

async function doCompare() {
  const b = $("compareWith").value;
  if (!S.run || !b) return;
  const c = await api(`/api/compare?a=${S.run.run_id}&b=${b}`);
  const col = (v) => h("div", { class: "card" },
    h("h3", {}, `${v.run.run_id} · ${v.run.status}`),
    h("div", { class: "small muted" }, `ontology v${v.run.ontology_version} · rules v${v.run.config_version} · ${v.steps} steps`),
    h("div", { class: "small" }, `asked: ${v.people_asked.join(", ") || "nobody"} · flags: ${v.flags.join(", ") || "none"}`),
    h("ol", {}, ...v.milestones.map((m) => h("li", { class: m.warnings.length ? "warned" : "" }, `[${m.stage}] ${m.summary}`))));
  $("compare").replaceChildren(col(c.a), col(c.b));
}

async function refreshMeta() {
  S.meta = await api("/api/meta");
}

// ------------------------------------------------------------ 6. editors
function bindEditors() {
  $("saveCfg").onclick = saveConfig;
  $("ontoFilter").oninput = () => renderOntologyTables();
}

let CFG = null, ONTO = null;
async function renderEditors() {
  const [cfg, onto] = await Promise.all([api("/api/config"), api("/api/ontology")]);
  CFG = cfg; ONTO = onto;
  $("cfgVersion").textContent = `v${cfg.version} · last change by ${cfg.author}: ${cfg.note}`;
  $("ontoVersion").textContent = `v${onto.version} · last change by ${onto.author}: ${onto.note}`;
  renderStages();
  renderOntologyTables();
}

const APPS = ["github", "gmail", "web", "genie"];
function renderStages() {
  const tools = Object.keys(S.meta.tools);
  $("stages").replaceChildren(...CFG.config.stages.map((st, si) => {
    const checks = (field, values) => h("div", { class: "checks" }, ...values.map((v) => h("label", {},
      h("input", { type: "checkbox", "data-stage": si, "data-field": field, value: v, checked: st[field].includes(v) }), v)));
    const rules = h("div", {}, ...st.rules.map((r, ri) => ruleRow(si, r)));
    return h("div", { class: "stage-card", "data-stage": si },
      h("h4", {}, st.name),
      h("label", { class: "small muted" }, "Goal"), h("textarea", { rows: 2, "data-goal": si }, st.goal),
      h("label", { class: "small muted" }, "Rules (plain language; optional structured check)"), rules,
      h("button", { class: "ghost small", onclick: () => rules.append(ruleRow(si, { id: `r_${Date.now() % 100000}`, text: "", enabled: true, check: null })) }, "+ add rule"),
      h("label", { class: "small muted" }, "Allowed tools"), checks("allowed_tools", tools),
      h("label", { class: "small muted" }, "May traverse node types"), checks("allowed_node_types", S.meta.node_types),
      h("label", { class: "small muted" }, "May traverse edge types"), checks("allowed_edge_types", S.meta.edge_types),
      h("label", { class: "small muted" }, "External apps"),
      h("div", { class: "checks" }, ...APPS.map((a) => h("label", {}, `${a} `,
        h("select", { "data-app": a, "data-stage": si }, ...["none", "read", "read_write"].map((l) => h("option", { value: l, selected: (st.apps[a] || "none") === l }, l)))))),
      h("label", { class: "small muted" }, "Escalation & stop"),
      h("div", { class: "checks" },
        h("label", {}, "ask timeout (s) ", h("input", { type: "number", min: 1, style: "width:70px", "data-esc": "ask_timeout_s", "data-stage": si, value: st.escalation.ask_timeout_s })),
        h("label", {}, "on timeout ", h("select", { "data-esc": "on_timeout", "data-stage": si },
          ...["escalate_to_second_source", "stop"].map((o) => h("option", { value: o, selected: st.escalation.on_timeout === o }, o)))),
        h("label", {}, "max steps ", h("input", { type: "number", min: 1, style: "width:60px", "data-esc": "max_steps", "data-stage": si, value: st.escalation.max_steps }))),
      h("input", { placeholder: "stop when…", "data-esc": "stop_when", "data-stage": si, value: st.escalation.stop_when || "" }));
  }));
}

function ruleRow(si, r) {
  return h("div", { class: "rule", "data-rule": si, "data-id": r.id },
    h("input", { type: "checkbox", class: "enabled", checked: r.enabled !== false, title: "enabled" }),
    h("input", { type: "text", class: "text", value: r.text, placeholder: "e.g. If an owner was last verified over 6 months ago, confirm with a second source." }),
    h("button", { class: "ghost small danger", onclick: (e) => e.target.closest(".rule").remove() }, "✕"),
    h("input", { type: "text", class: "check", value: r.check ? JSON.stringify(r.check) : "", placeholder: 'structured check (optional), e.g. {"type":"stale_owner","max_age_days":180}' }));
}

async function saveConfig() {
  const cfg = structuredClone(CFG.config);
  try {
    cfg.stages.forEach((st, si) => {
      st.goal = document.querySelector(`[data-goal="${si}"]`).value;
      for (const field of ["allowed_tools", "allowed_node_types", "allowed_edge_types"]) {
        st[field] = [...document.querySelectorAll(`input[data-stage="${si}"][data-field="${field}"]:checked`)].map((x) => x.value);
      }
      st.apps = {};
      document.querySelectorAll(`select[data-app][data-stage="${si}"]`).forEach((s) => { if (s.value !== "none") st.apps[s.dataset.app] = s.value; });
      document.querySelectorAll(`[data-esc][data-stage="${si}"]`).forEach((x) => {
        st.escalation[x.dataset.esc] = x.type === "number" ? Number(x.value) : x.value;
      });
      st.rules = [...document.querySelectorAll(`.rule[data-rule="${si}"]`)].filter((r) => r.querySelector(".text").value.trim()).map((r) => ({
        id: r.dataset.id, text: r.querySelector(".text").value.trim(), enabled: r.querySelector(".enabled").checked,
        check: r.querySelector(".check").value.trim() ? JSON.parse(r.querySelector(".check").value) : null,
      }));
    });
  } catch (e) { return toast("A structured check is not valid JSON.", true); }
  try {
    const res = await api("/api/config", { method: "PUT", body: { config: cfg, author: S.me, note: $("cfgNote").value || "rule editor" } });
    toast(`Saved as rules v${res.version}.`);
    $("cfgNote").value = "";
    renderEditors();
  } catch (e) { toast(e.message, true); }
}

function renderOntologyTables() {
  if (!ONTO) return;
  const q = $("ontoFilter").value.toLowerCase();
  const ids = ONTO.nodes.map((n) => n.id);
  const lbl = (id) => (ONTO.nodes.find((n) => n.id === id) || { label: id }).label;
  const sel = (vals, v, cls) => h("select", { class: cls }, ...vals.map((x) => h("option", { value: x, selected: x === v }, x)));
  const opsCall = async (ops, note) => {
    try { const r = await api("/api/ontology/ops", { method: "POST", body: { ops, author: S.me, note } }); toast(`Saved as ontology v${r.version}.`); renderEditors(); }
    catch (e) { toast(e.message, true); }
  };
  const edgeRow = (e) => {
    const tr = h("tr", { class: e && ageDays(e.last_verified) > 180 ? "stale" : "" },
      h("td", {}, e ? h("span", { title: `${lbl(e.src)} ${e.type} ${lbl(e.dst)}` }, e.id) : h("i", { class: "muted" }, "new edge")),
      h("td", {}, sel(ids, e?.src, "src")), h("td", {}, sel(S.meta.edge_types, e?.type, "type")), h("td", {}, sel(ids, e?.dst, "dst")),
      h("td", {}, h("input", { class: "source", value: e?.source || "manual edit" })),
      h("td", {}, h("input", { class: "confidence", type: "number", step: "0.05", min: 0, max: 1, value: e?.confidence ?? 0.8, style: "width:64px" })),
      h("td", {}, h("input", { class: "last_verified", type: "date", value: e?.last_verified || S.meta.today }),
        e ? h("div", { class: "small muted" }, `${ageDays(e.last_verified)} days`) : null),
      h("td", {}, h("div", { class: "row" },
        h("button", { class: "small", onclick: () => {
          const v = (c) => tr.querySelector("." + c).value;
          const fields = { src: v("src"), type: v("type"), dst: v("dst"), source: v("source"), confidence: Number(v("confidence")), last_verified: v("last_verified") };
          opsCall(e ? [{ op: "update_edge", id: e.id, set: fields }] : [{ op: "add_edge", edge: fields }], e ? `edit edge ${e.id}` : "add edge");
        } }, e ? "save" : "add"),
        e ? h("button", { class: "small danger", onclick: () => opsCall([{ op: "delete_edge", id: e.id }], `delete edge ${e.id}`) }, "delete") : null)));
    return tr;
  };
  const edges = ONTO.edges.filter((e) => !q || `${e.id} ${lbl(e.src)} ${e.type} ${lbl(e.dst)} ${e.source}`.toLowerCase().includes(q));
  $("edgesTable").replaceChildren(h("tr", {}, ...["id", "src", "type", "dst", "source", "confidence", "last verified", ""].map((t) => h("th", {}, t))),
    edgeRow(null), ...edges.map(edgeRow));

  const nodeRow = (n) => {
    const tr = h("tr", {},
      h("td", {}, n ? n.id : h("input", { class: "id", placeholder: "new_id" })),
      h("td", {}, sel(S.meta.node_types, n?.type, "type")),
      h("td", {}, h("input", { class: "label", value: n?.label || "" })),
      h("td", {}, h("input", { class: "props", value: JSON.stringify(n?.props || {}) })),
      h("td", {}, h("div", { class: "row" },
        h("button", { class: "small", onclick: () => {
          let props; try { props = JSON.parse(tr.querySelector(".props").value || "{}"); } catch (_) { return toast("props must be JSON", true); }
          const fields = { type: tr.querySelector(".type").value, label: tr.querySelector(".label").value, props };
          opsCall(n ? [{ op: "update_node", id: n.id, set: fields }] : [{ op: "add_node", node: { id: tr.querySelector(".id").value, ...fields } }], n ? `edit node ${n.id}` : "add node");
        } }, n ? "save" : "add"),
        n ? h("button", { class: "small danger", onclick: () => opsCall([{ op: "delete_node", id: n.id }], `delete node ${n.id}`) }, "delete") : null)));
    return tr;
  };
  const nodes = ONTO.nodes.filter((n) => !q || `${n.id} ${n.label} ${n.type}`.toLowerCase().includes(q));
  $("nodesTable").replaceChildren(h("tr", {}, ...["id", "type", "label", "props", ""].map((t) => h("th", {}, t))), nodeRow(null), ...nodes.map(nodeRow));
}

// ------------------------------------------------------------ 7. history
async function renderHistory() {
  const [ov, cv, audit, tcs, instr] = await Promise.all([api("/api/ontology/versions"), api("/api/config/versions"),
    api("/api/audit"), api("/api/testcases"), api("/api/config/instructions")]);
  const versions = (list, kind) => list.slice().reverse().map((v, i) => h("li", {},
    h("span", {}, h("b", {}, `v${v.version}`), ` · ${shortTime(v.created_at)} · ${v.author} · ${v.note}`),
    i === 0 ? chip("good", "current") : h("button", { class: "small", onclick: async () => {
      const r = await api(`/api/${kind}/rollback`, { method: "POST", body: { version: v.version, author: S.me } });
      toast(`Rolled back: new ${kind === "ontology" ? "ontology" : "rules"} v${r.version}.`); renderHistory();
    } }, "roll back to this")));
  $("ontoVersions").replaceChildren(...versions(ov, "ontology"));
  $("cfgVersions").replaceChildren(...versions(cv, "config"));
  $("auditTable").replaceChildren(h("tr", {}, ...["when", "who", "action", "target", "note"].map((t) => h("th", {}, t))),
    ...audit.map((a) => h("tr", {}, h("td", {}, shortTime(a.created_at)), h("td", {}, a.actor), h("td", {}, a.action),
      h("td", {}, a.target), h("td", { title: pretty({ before: a.before, after: a.after }) }, a.note || ""))));
  $("testCases").replaceChildren(...(tcs.length ? tcs.map((t) => h("li", {}, h("span", {}, `${t.task.slice(0, 40)} → "${t.expected_outcome}"`), h("span", { class: "muted" }, t.marked_by))) : [h("li", { class: "muted" }, "Runs you mark wrong are saved here.")]));
  $("instructions").textContent = instr.markdown;
}

// ------------------------------------------------------------ 8. benchmark
async function renderBenchmark() {
  const b = await api("/api/benchmark");
  if (b.status === "not run") {
    $("benchNote").textContent = "Compares no ontology, Genie only, our ontology, and our ontology after accepting the proposed fixes, over seeded failures.";
    $("benchSummary").replaceChildren(); $("benchTable").replaceChildren();
    return;
  }
  if (b.status === "running") { $("benchNote").textContent = `Running… ${b.progress || ""}`; setTimeout(renderBenchmark, 1200); return; }
  $("benchNote").textContent = b.note || "";
  $("benchSummary").replaceChildren(...b.conditions.map((c) => {
    const s = b.summary[c.id];
    const bar = (v) => h("div", { class: "bar" }, h("div", { style: `width:${Math.round(v * 100)}%` }));
    return h("div", { class: "bench-cond" }, h("b", {}, c.label), h("div", { class: "small muted" }, c.description),
      s.skipped ? h("p", { class: "small" }, `skipped: ${s.skipped}`) : h("div", {},
        h("div", { class: "small" }, `accuracy ${Math.round(s.accuracy * 100)}% (${s.correct}/${s.total})`), bar(s.accuracy),
        h("div", { class: "small" }, `claims with evidence ${Math.round(s.evidence_share * 100)}%`), bar(s.evidence_share),
        h("div", { class: "small" }, `avg steps ${s.avg_steps.toFixed(1)}`), bar(Math.min(1, s.avg_steps / 30))));
  }));
  $("benchTable").replaceChildren(h("tr", {}, h("th", {}, "case"), h("th", {}, "seeded failure"), h("th", {}, "expected"), ...b.conditions.map((c) => h("th", {}, c.label))),
    ...b.cases.map((cs) => h("tr", {}, h("td", {}, cs.question), h("td", {}, cs.failure), h("td", {}, cs.expected_label),
      ...b.conditions.map((c) => {
        const r = b.results[c.id][cs.id];
        if (!r || r.skipped) return h("td", { class: "muted" }, "-");
        return h("td", { class: r.correct ? "ok" : "no", title: `${r.steps} steps · run ${r.run_id}` }, `${r.correct ? "✓" : "✗"} ${r.outcome_label}`);
      }))));
}

async function runBenchmark() {
  await api("/api/benchmark/run", { method: "POST" });
  $("benchNote").textContent = "Running…";
  setTimeout(renderBenchmark, 800);
}

boot().catch((e) => { console.error(e); toast(`Failed to start: ${e.message}`, true); });
