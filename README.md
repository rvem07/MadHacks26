# Agent Watcher

The default screen is a **guided wildlife-protection demo** with just a prompt, the existing employee graph, and a summarized workflow. Plan a secure application for an endangered-animal startup without exposing precise animal locations to hunters. The demo reuses FastAPI, Cytoscape, SSE, and the durable human-question workflow. The generic Watcher backend remains available through its APIs; the original Agent Flow Debugger is opt-in with `AFD_WORKFLOW=legacy`.

## Run locally

Python 3.11+:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python scripts/run_pipeline.py
.venv/bin/python -m afd
```

Open **http://localhost:8765**. Local mode needs no Databricks credentials. It stores authoritative documents under `data/watcher/`; initial startup publishes the sample if no batch exists. Explicit pipeline runs always append a new immutable batch. Files in `samples/badgernet/` are checked in; `data/` is ignored.

The screen is labeled **guided demo**. Its employee sequence and questions are deterministic and need no model credentials, including on Databricks. Questions, human selections, follow-ups, and the final specification are real persisted workflow records. Generic runs without a scenario identifier can still use the local or Databricks model adapter described below.

## Manual demo

1. Use the prefilled wildlife-protection prompt, or enter **Build an application for our startup**. Click **Start demo**.
2. The scenario explicitly starts with **Maria Alvarez** for primary users, then **Priya Sharma** for sighting collection, **Rachel Kim** for location access, **Lena Ortiz** for field alerts, and **Tom Becker** for the pilot. It never falls back to Aisha because of dropdown ordering.
3. Answer each employee's short question inside the workflow panel. Each question has three choices plus **Other** with an explanation field.
4. To demonstrate a follow-up, choose Other and type **Not sure, whatever works**. The agent stays with the same employee and asks for a concrete decision. Choose a specific option, or explain **Field rangers and local veterinarians**, to continue.
5. Refresh while waiting: the same saved question and prior answers return in that browser. No answer or advance is driven by a timer.
6. After all five decisions, read the application specification in the workflow panel. It summarizes the supplied answers and a security foundation; it does not claim to have deployed an application. Click **Run again** to repeat.

All 14 existing employee nodes and 9 reporting edges are retained. Consulted employees turn green; the employee awaiting an answer turns gold. Only actual reporting edges pulse when traversed; no scenario-only reporting relationships are fabricated.

The screen has no Act-as switcher, separate inbox, run history, pipeline panel, replay, raw-event viewer, Nudge, Reroute, Cancel, or administration controls. Answers are explicitly attributed to the employee named in the question. This is a demo interaction, not employee authentication.

Scenario definition: `afd/wildlife.py`, version `wildlife_protection_v1`. Choice answers resolve the current decision. Other explanations use conservative stage-specific rules to recognize concrete answers; vague or unrecognized explanations prompt a narrower follow-up. The demonstrator can always use a structured choice to clarify the decision. These rules are not general-purpose semantic understanding.

## Pipeline and data-quality rules

The same Python transformations run here and as a Databricks Job. No Spark installation or extra runtime dependency is required.

| Layer | Behavior |
|---|---|
| Bronze | Preserve original CSV values, source file/line, ingestion time and batch; insert-only projection. |
| Silver | Trim/lowercase emails; merge repeated canonical emails; apply explicit aliases; preserve provenance. |
| Quarantine | Retain raw records and reasons for missing managers, multiple managers, reporting cycles, and unowned topics. |
| Gold | Employee nodes with email IDs; validated `reports_to` edges; topic ownership and materialized owner-to-manager chains. |

`aliases.json` explicitly merges `psharma@badgernet.com` into `priya.sharma@badgernet.com`. It is an auditable input decision, not an inferred identity claim. Canonical-email rows take precedence, then the most complete name, then source-row order. Conflicting manager edges are all quarantined instead of selecting a winner. Cycle participants keep their employee nodes but lose invalid reporting edges. A blank manager is allowed for a root employee.

Expected sample output: **24 raw records, 2 duplicates merged, 14 employees, 9 reporting edges, 7 owned topics, 6 quarantined records in 4 violation groups**. Quarantine: 1 missing manager, 2 multiple-manager records, 2 cycle records, and 1 unowned topic. Teams are employee attributes; topics live in routing data, not graph nodes. The default wildlife demo supplies explicit scenario responsibility mappings without changing these employee nodes or reporting edges. Generic routing still uses the CSV topics.

Each pipeline publication creates a batch and updates the active pointer only after gold projections succeed. Runs pin their batch. Query gold routing on Databricks with:

```sql
SELECT topic, owner_email, person_email, depth
FROM workspace.agent_watcher.topic_routing
WHERE topic = 'network outage'
ORDER BY depth;
```

The result is Priya → Tom → Maria. Invalid reporting relationships never enter that chain. Nodes and edges carry source, confidence, and UTC update time. Unity Catalog primary/foreign keys document relationships; code performs integrity validation because [Databricks key constraints are informational](https://docs.databricks.com/aws/en/tables/constraints).

## Checkpoints, requests, and events

`afd/watcher.py` is the active workflow policy and `afd/watchstore.py` implements persistence. The existing coding/Agent Bricks host is retained for the legacy workflow; Watcher reuses the existing Databricks model client directly for bounded JSON decisions.

Each run document atomically stores its checkpoint, full conversation, selected next action, pending-request history, and event outbox. Human waiting returns immediately after saving; it retains no agent thread, HTTP callback, or model invocation. Answer submission launches a fresh execution segment. The only automatic read loops are UI observation and SQL transport completion checks, neither of which escalates or resumes human waiting.

States: `running`, `waiting_on_human`, `resumed`, `done`, `cancelled`. Generic model errors retain the checkpoint with an explicit diagnostic and a backend retry endpoint; they do not invent an answer. A saved next action is replayed after restart rather than asking the model to choose again. Waiting runs remain waiting after restart.

Databricks mode synchronously writes authoritative `workflow_documents` using revision checks. Physical `runs`, `pending_requests`, and `events` Delta tables are idempotent projections from those documents; projection failures are surfaced and repaired on startup. Run **one app process/replica**: initial-key uniqueness and continuation dispatch depend on this single-writer deployment. This is not a distributed task queue. Local JSON persistence uses atomic replacement and file locking.

If a projection fails after an authoritative transition committed, the API can report an error even though the answer is saved. Use the backend repair endpoint or repair projections and restart the app to recover ready continuations; administration controls are intentionally absent from the simplified demo. Cancelled and superseded requests reject later answers. For manual projection repair without ingesting a new batch:

```bash
.venv/bin/python scripts/run_pipeline.py --repair
```

Watcher API: `/api/watcher/meta`, `/pipeline?batch=...`, `/runs`, `/runs/{id}`, `/runs/{id}/stream`, `/pending_requests?person=...`, `/pending_requests/{id}/answer`, and `/runs/{id}/{nudge|reroute|cancel|retry|repair}`. Start payload: `{task, requester, scenario?}`; text answer: `{answer, person}`; demo answer: `{option_id, explanation?, person}`; controls: `{actor}`. Use `scenario: "wildlife_protection_v1"` for the guided workflow; omit it for generic routing. Existing `/api/runs`, `/api/ontology`, `/api/questions` and reply routes dispatch to Watcher by default. Legacy-only editors, review, and benchmarks are hidden in the Watcher UI.

## Deploy later to Databricks

No workspace access is needed to code or test locally. `databricks.yml` defines a serverless pipeline Job and a Databricks App; `app.yaml` binds an existing SQL warehouse and model endpoint. The app installs the existing `requirements.txt`. The Job needs only the Databricks SDK and imports the shared pipeline, not the web app.

When workspace access is available:

1. Authenticate the Databricks CLI using your workspace's approved OAuth/profile flow. No credentials belong in this repo.
2. Choose a SQL warehouse, Unity Catalog catalog/schema, and a chat model endpoint that accepts the existing OpenAI-compatible serving payload. Update `app.yaml` catalog/schema if changing the bundle defaults; the app and Job must use the same schema. `model_endpoint` is passed to the app through its resource binding.
3. Grant the pipeline identity warehouse access and catalog/schema creation or ownership permissions needed for bootstrap. Grant the app service principal `USE CATALOG`, `USE SCHEMA`, and `SELECT`/`MODIFY` on the created tables. Warehouse `CAN_USE` and model `CAN_QUERY` bindings are included in the bundle. Schema/view creation remains a pipeline responsibility.
4. Validate, deploy resource definitions, publish the pipeline, grant the app table access, then start the App:

```bash
databricks bundle validate --var warehouse_id=YOUR_WAREHOUSE_ID
databricks bundle deploy --var warehouse_id=YOUR_WAREHOUSE_ID
databricks bundle run watcher_pipeline --var warehouse_id=YOUR_WAREHOUSE_ID
# Grant the App service principal access to the created schema/tables before starting it.
databricks bundle run watcher --var warehouse_id=YOUR_WAREHOUSE_ID
```

[Bundle deployment uploads the source; running the app starts/restarts it](https://docs.databricks.com/aws/en/dev-tools/cli/bundle-commands). Check the app's actual health/logs after starting it. [Resource-backed environment variables](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/environment-variables) resolve warehouse IDs and model endpoint names without hardcoding them in application code.

Alternatively, bootstrap from this checkout after authenticating:

```bash
.venv/bin/python scripts/run_pipeline.py --delta --bootstrap \
  --warehouse-id YOUR_WAREHOUSE_ID --catalog workspace --schema agent_watcher
```

Bootstrap is idempotent and uses a **separate `agent_watcher` schema**, leaving original `agent_flow` tables intact. It creates physical bronze, silver, gold, quarantine, routing, stats, request, event, run, and checkpoint tables, plus current-batch views. There is no destructive migration or conversion of old demo runs. For future incompatible Watcher schema changes, publish to a new schema and update both app and Job configuration; bootstrap does not silently rewrite existing columns.

For hosted-model testing from local code, set `AFD_WATCHER_AGENT=databricks` and `AFD_WATCHER_MODEL=<endpoint>` with SDK authentication available. Set `AFD_DELTA=1`, `AFD_WAREHOUSE_ID`, `AFD_CATALOG`, and `AFD_WATCHER_SCHEMA` to use Delta persistence. Databricks mode refuses local-only fallback and requires a published pipeline before app startup. App-local files are temporary and are not used as the workflow authority.

Optional tracing: configure a writable MLflow experiment, `MLFLOW_TRACKING_URI=databricks`, `MLFLOW_EXPERIMENT_ID`, and `AFD_MLFLOW=1`. Spans cover actual routing calls and execution segments. The semantic event log remains available without MLflow.

## Verification

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/validate_bundle.py  # installed Databricks CLI schema; offline
node --check static/app.js
node --check static/watcher.js

# Optional browser acceptance test against the running LOCAL app:
.venv/bin/pip install -r requirements-browser.txt
# If system Chrome is unavailable: .venv/bin/python -m playwright install chromium
.venv/bin/python scripts/verify_watcher_browser.py
```

The browser check verifies the three-panel screen, unchanged graph, explicit Maria routing, multiple-choice answers, Other follow-ups, refresh recovery, all five employee decisions, final specification, mobile layout, and Run again. It writes a screenshot under ignored `data/`. Backend tests cover restart recovery, pinned batches, stale answers, concurrent controls, model decisions through an injected adapter, and projection-failure recovery. See `docs/watcher-verification.md` for executed results and remaining workspace checks.

---

# Original Agent Flow Debugger (legacy workflow)

The following documentation describes `AFD_WORKFLOW=legacy`, including its historical simulated replies and timeout behavior. Those mechanisms are not used by Agent Watcher.


# Agent Flow Debugger

**A glass-box control room for AI agents at small startups.** Teammates watch an agent move through the
company's ontology graph in real time, read a plain-language log of each decision, review the run
afterwards to find where it went wrong, and fix the agent's rules and the ontology with AI-proposed
diffs that a human accepts, edits, or rejects.

Built for Databricks: the app is a Databricks App, the ontology and every event live in Unity Catalog
Delta tables, the narrator and reviewer run on Databricks-hosted models, the LLM agent is built with
the **Agent Bricks CLI**, and Genie answers data questions as one of the agent's tools.

> The product makes the agent's **actions and stated reasoning auditable**. It does not claim to reveal
> the model's internal reasoning. That is why every piece of content in the UI carries a fidelity label:
> **observed** (tool calls and the data they returned), **reported** (the agent's own stated rationale),
> **inferred** (our narrator sentences, flags and proposals).

---

## Contents

1. [The 2-minute demo](#1-the-2-minute-demo)
2. [Quick start](#2-quick-start)
3. [Architecture](#3-architecture)
4. [The ontology](#4-the-ontology)
5. [The agent: stages, config, hosts](#5-the-agent-stages-config-hosts)
6. [The tool wrapper and action limits](#6-the-tool-wrapper-and-action-limits)
7. [Human in the loop](#7-human-in-the-loop)
8. [Events, fidelity labels and narration](#8-events-fidelity-labels-and-narration)
9. [Post-mortem: flags, marking, proposals, rerun](#9-post-mortem-flags-marking-proposals-rerun)
10. [The UI](#10-the-ui)
11. [Benchmark](#11-benchmark)
12. [Databricks setup and deployment](#12-databricks-setup-and-deployment)
13. [Configuration reference](#13-configuration-reference)
14. [HTTP API](#14-http-api)
15. [Tests](#15-tests)
16. [Decisions, verification status and limits](#16-decisions-verification-status-and-limits)
17. [Repository layout](#17-repository-layout)

---

## 1. The 2-minute demo

The decided scenario is the **stale owner** (spec section 13). Lumen Ledger is a fictional seven-person
invoicing startup. The ontology says *"Priya Shah owns Security specs"*, but that edge was last
verified on 2025-08-01, more than a year ago, and came from an old onboarding doc. Priya has since
moved to the Data team, and Marcus Reed now leads security.

| # | Say | Do |
|---|-----|----|
| 1 | "Any teammate can give our agent a task." | **Run** tab → task *"Build the backend for the invoicing feature"* → **Run agent**. |
| 2 | "You can watch it walk our company graph." | The graph lights up: task → Invoicing service → Security specs → Priya. The log narrates each milestone in plain language. |
| 3 | "It needs the security rules, which aren't readable, so it asks the owner and pauses." | The inbox shows *Question for Priya Shah*, mirrored to Gmail as a dry run. Priya's reply comes back plausible but out of date (HS256, 180-day rotation). |
| 4 | "It builds on that answer and opens a PR, on its own branch, never main." | The log shows the branch, the files and *Opened pull request #1*. |
| 5 | "So what went wrong?" | **Review** tab: the flag *Relied on a stale edge: Priya Shah owns Security specs* (inferred), with the edge drawn red and dashed on the graph. |
| 6 | "The AI proposes two fixes as diffs, and nothing applies until a human decides." | *Point the edge to Marcus Reed* (ontology) and *Add rule: if an owner was last verified over 6 months ago, confirm with a second source* (rules). **Accept** both. |
| 7 | "Same task again." | **Rerun this task**. The live view warns *"Recently corrected: this edge said 'Priya Shah owns Security specs' (stale)…"* before asking. It asks **Marcus**, and the code now uses RS256 with 90-day rotation. **compare** shows both runs side by side. |
| 8 | "Every change is versioned and attributed, and one click undoes it." | **History** tab: ontology v2 / rules v2 by the person who accepted, with an audit log and rollback. |

Optional: **mark the first run wrong** (*"It should have asked Marcus"*). The system localizes the first
divergent step (the decision that chose Priya), notes that *Marcus never appeared on the agent's path*,
and saves the run as a test case.

---

## 2. Quick start

Requirements: Python 3.11+. Databricks access is **not** needed for the offline demo.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

# Offline, deterministic demo: simulated teammates answer after 3 seconds.
AFD_AUTO_REPLY_S=3 AFD_TODAY=2026-11-14 .venv/bin/uvicorn afd.main:app --port 8765
# open http://127.0.0.1:8765
```

- On first start the app seeds `data/` with ontology v1, rules v1 and the simulated repo.
  Delete `data/` to reset the demo.
- `AFD_TODAY` pins "today" so edge ages match the scenario. Omit it to use the real date.
- Leave `AFD_AUTO_REPLY_S` unset (or `-1`) to answer the agent's questions yourself in the inbox.
- Deep links open a tab directly: `/#review`, `/#editors`, `/#history`, `/#benchmark`.

Run the tests and the benchmark:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/benchmark.py
```

---

## 3. Architecture

```
                         ┌───────────────────────── Databricks App (afd) ─────────────────────────┐
  teammate's browser ───▶│  static UI (Cytoscape.js)  ◀── SSE ──  FastAPI  (afd/main.py)           │
                         │                                         │                               │
                         │                         RunManager (afd/runs.py)                        │
                         │          pins ontology + rules versions, runs the host, narrates,       │
                         │          flags, reviews, rerun / compare                                │
                         │                 │                               ▲                       │
                         │        host = scripted                 host = agentbricks               │
                         │   ScriptedPolicy (afd/policy.py)    POST /invocations  ─────────┐       │
                         │                 │                               │                │       │
                         │                 ▼                               │                │       │
                         │   ToolWrapper (afd/tools.py)  ◀── POST /api/agent-tools/{tool} ─┼──┐    │
                         │   allowlists · protected main · blocked events · milestones     │  │    │
                         │      │          │           │           │                       │  │    │
                         │  ontology    repo (sim     human       Genie                     │  │    │
                         │  (versioned) or GitHub)   channel     (read-only)                │  │    │
                         │                          in-app +                                │  │    │
                         │                          Gmail dry-run                           │  │    │
                         │      LocalStore (working state) ── write-behind ──▶ Delta (UC)   │  │    │
                         └──────────────────────────────────────────────────────────────────┼──┼────┘
                                                                                            │  │
                    ┌──────── Agent Bricks agent (agent_bricks/, Databricks App) ◀──────────┘  │
                    │  OpenAI Agents SDK + databricks-qwen3-next-80b-a3b-instruct              │
                    │  instructions  = GET /api/config/instructions (rendered from rules)      │
                    │  tools         = GET /api/agent-tools, each call ─────────────────────────┘
                    └────────────────────────────────────────────────────────────────────────
  Narrator: databricks-meta-llama-3-1-8b-instruct · Reviewer: databricks-gpt-oss-120b (both optional)
```

**Every agent action, whichever host drives it, goes through one `ToolWrapper.call()`.** That is
the single place where allowlists are enforced and where the one-event-per-tool-call trace is written.
The Agent Bricks agent has no other path to GitHub, Gmail or the ontology.

### Module map

| Module | Responsibility |
|---|---|
| `afd/config.py` | Environment-driven settings (every env var is listed in [section 13](#13-configuration-reference)). |
| `afd/store.py` | `LocalStore` (thread-safe JSON per table) + `DeltaMirror` (write-behind to UC) + `hydrate_from_delta`. |
| `afd/audit.py` | Who-changed-what log shared by every editor. |
| `afd/ontology.py` | Versioned graph (full snapshot per version), edit ops, rollback, `Graph` helpers. |
| `afd/agentcfg.py` | Versioned per-stage agent config, edit ops, rollback, Markdown instruction rendering. |
| `afd/seed.py` | The Lumen Ledger ontology, rules v1, and the simulated repo's files. |
| `afd/toolspecs.py` | Tool catalogue: descriptions, params, action type, app + level, graph marker. |
| `afd/tools.py` | **The tool wrapper** and every tool implementation. |
| `afd/events.py` | Event model and fidelity labels. |
| `afd/channels.py` | Human-in-the-loop: in-app question queue with blocking waits, Gmail mirror, simulated teammates. |
| `afd/repo.py` | The one repo: simulated (default) or GitHub REST; protected `main`. |
| `afd/policy.py` | The deterministic scripted agent (demo, tests, benchmark). |
| `afd/agentbricks.py` | Agent Bricks host: invokes `agent_bricks/` and routes its tool callbacks into the wrapper. |
| `afd/llm.py` | Databricks model-serving chat client (no external API key). |
| `afd/narrator.py` | Milestone narration with grounding check and template fallback. |
| `afd/flags.py` | Automatic post-mortem flags + divergence localization. |
| `afd/reviewer.py` | Fix proposals as before/after diffs (rules engine, or the review model). |
| `afd/runs.py` | Run lifecycle, pause/reply, timeouts, marking, proposals, rerun, compare, MLflow spans. |
| `afd/genie.py` | Genie conversation call used by the `genie_query` tool. |
| `afd/benchmark.py` | The four-condition benchmark. |
| `afd/main.py` | FastAPI: REST, server-sent events, static UI, the Agent Bricks tool bridge. |
| `agent_bricks/` | The LLM agent, scaffolded with `agentbricks init` (OpenAI Agents SDK, custom server). |
| `static/` | The UI: `index.html`, `style.css`, `app.js` (no build step). |
| `sql/schema.sql` | Unity Catalog DDL with table and column comments, plus views. |
| `scripts/` | `setup_delta.py`, `setup_genie.py`, `benchmark.py`. |

---

## 4. The ontology

### Model

- **Nodes** `{id, type, label, props}` with `type ∈ person, team, service, repo_area, ticket, task, document`.
- **Edges** `{id, src, dst, type, source, confidence, last_verified, props}` with
  `type ∈ owns, member_of, depends_on, affects, works_on, responsible_for`.
- **Every edge carries provenance:** `source` (where the fact came from), `confidence` (0–1) and
  `last_verified` (a date). `owns` and `responsible_for` express accountability.

The seed (`afd/seed.py`) has seven people, four teams, six services, four repo areas, six documents,
three tickets, two tasks and 38 edges. Besides the demo's stale edge, it carries the benchmark's
seeded failures (section 11). Every email uses the reserved `.example` domain.

### Versioning

Every change writes a **full snapshot** as a new integer version. Snapshots are tiny, and full
snapshots make replay trivial: a run records its `ontology_version`, and the UI renders the graph
**as it was** for that run. **Rollback never deletes history.** It writes a new version equal to an
old one.

Edits are expressed as ops, and AI proposals use exactly the same format:

```json
[{"op": "update_edge", "id": "e_priya_owns_secspec",
  "set": {"src": "p_marcus", "confidence": 0.9, "last_verified": "2026-11-14",
          "source": "second source: Marcus Reed responsible for security/ (CODEOWNERS)"}}]
```

Ops: `add_node`, `update_node`, `delete_node` (removes its edges too), `add_edge`, `update_edge`,
`delete_edge`. Every op batch is validated (known types, no dangling edges, confidence in 0..1, ISO
dates, provenance present) and applied atomically.

---

## 5. The agent: stages, config, hosts

### Stages

One agent with four stages. Asking a person is a handoff, not a second agent.

| Stage | Goal | Tools (defaults) | In the demo |
|---|---|---|---|
| **triage** | Understand the task; find its node and what it affects. | `ontology_lookup`, `ontology_traverse`, `record_decision` | Task node → `affects` → Invoicing service. |
| **investigate** | Find the documents, services and people the work depends on; ask a human for anything unreadable. | + `cite_evidence`, `ask_human`, `read_file`, `analyze_csv`, `web_search`, `genie_query` | `depends_on` → Security specs → `owns` → Priya; asks her and waits. |
| **act** | Do the work on `agent/<task-id>`, open a PR, never push to main. | `read_file`, `edit_file`, `analyze_csv`, `github_create_branch`, `github_open_pr`, `message_human` | Writes `services/invoicing/*`, opens the PR, messages the requester. |
| **verify** | Check the work and cite evidence for every claim. | `read_file`, `cite_evidence`, `record_decision`, `conclude` | Concludes with 4 claims, each backed by evidence. |

### The agent is data (versioned, editable)

Per stage (`afd/agentcfg.py`):

```json
{
  "name": "investigate",
  "goal": "Find the documents, services and people the work depends on...",
  "allowed_node_types": ["person", "team", "service", "repo_area", "ticket", "task", "document"],
  "allowed_edge_types": ["owns", "member_of", "depends_on", "affects", "works_on", "responsible_for"],
  "allowed_tools": ["begin_stage", "ontology_lookup", "ontology_traverse", "record_decision", "cite_evidence",
                    "ask_human", "read_file", "analyze_csv", "web_search", "genie_query"],
  "rules": [
    {"id": "r_ask_owner", "text": "When a required document is not readable, ask its owner rather than guessing.",
     "enabled": true, "check": null},
    {"id": "r_stale_owner", "text": "If an owner was last verified over 6 months ago, confirm with a second source before asking them.",
     "enabled": true, "check": {"type": "stale_owner", "max_age_days": 180, "require_second_source": true}}
  ],
  "escalation": {"ask_timeout_s": 120, "on_timeout": "escalate_to_second_source", "max_steps": 30,
                 "stop_when": "no owner can be found for a required document"},
  "apps": {"github": "read", "gmail": "read_write", "web": "read", "genie": "read"}
}
```

Each rule has two halves:

- **`text`** is the plain-language rule teammates read and edit. It is what the LLM agent is instructed with.
- **`check`** (optional) is a structured version that the tool wrapper and the scripted policy enforce or
  act on deterministically. The supported checks are `stale_owner`, `fallback_repo_owner`, `no_revisit`,
  `require_evidence` and `min_confidence`.

Rules v1 deliberately has **no** stale-owner rule, so the demo's first run goes wrong.

`GET /api/config/instructions` renders the config as the Markdown the LLM agent receives. You can
view it in **History**.

### Two hosts, one wrapper

| Host | What drives the agent | Use it for |
|---|---|---|
| `scripted` (default) | `afd/policy.py`: a deterministic policy that **reads the config** and acts on the structured checks. It is not a fake: it calls the same tools through the same wrapper. | Reproducible demos, tests, the benchmark. |
| `agentbricks` | The LLM agent in `agent_bricks/` (Agent Bricks CLI, OpenAI Agents SDK, `databricks-qwen3-next-80b-a3b-instruct` by default). | The real LLM-driven agent. |

Pick the host with `AFD_AGENT_HOST`, or per run with `POST /api/runs {"host": "agentbricks"}`.

**How the Agent Bricks host works** (`afd/agentbricks.py`, `agent_bricks/agent/agent.py`):

1. The run manager creates the run, pins its versions and registers the run's wrapper.
2. It POSTs `{"input": [task], "run_id": ...}` to the agent's `/invocations`.
3. The agent fetches its **instructions** from `/api/config/instructions` and its **tool catalogue** from
   `/api/agent-tools`, on every invocation. A rule edit therefore takes effect on the next run without a
   redeploy.
4. Every tool call is POSTed back to `/api/agent-tools/{tool}` with the run id bound by the agent code, not
   chosen by the model. It goes through the same `ToolWrapper.call()`.
5. `ask_human` blocks inside that callback until a teammate replies, so the agent pauses naturally.

**Why not the Agent Bricks Supervisor Agent?** It was the original plan, but on 2026-11-14 the
workspace refused to create one: *"Supervisor Agent is now a legacy feature. New instances can no
longer be created for this account. Please use Genie Agents instead."* Genie Agents (the new name for
Genie spaces) are read-only data Q&A, so they cannot open PRs. The **Agent Bricks CLI**
(`databricks-agentbricks`) is Databricks' current code-first way to build custom agents. It scaffolds
the project, runs it locally (`agentbricks dev`), and deploys it to Databricks Apps with MLflow tracing.
That is what `agent_bricks/` is.

The OpenAI Agents SDK exports traces to OpenAI by default. `agent_bricks/agent/agent.py` turns that
off (`set_tracing_disabled(True)`), so run data stays in Databricks.

---

## 6. The tool wrapper and action limits

`afd/tools.py` → `ToolWrapper.call(name, args)`. In order, it:

1. Rejects unknown tools and any call before `begin_stage`.
2. Checks the tool against the stage's **`allowed_tools`**.
3. Checks the tool's external app against the stage's **app allowlist**: each tool declares `app` and
   `level`. For example, `edit_file` is `github` + `write` and needs `read_write`.
4. Enforces the stage's **step budget** (`escalation.max_steps`). `conclude` is always allowed: a budget
   stops further work, never the report of what was done.
5. Runs the tool against the run's **pinned ontology version**. Traversal is limited to the stage's
   allowed node and edge types.
6. On `ask_human`, records **which edge made the agent pick that person**. An LLM may cite a context edge
   (for example *service depends_on doc*) instead of the ownership edge. So any ownership edge from that person
   which the agent actually traversed is attached first and listed in `result.edges_inferred` (inferred). The
   stale-edge flag then sees what the choice really relied on.
7. Applies structured rule checks: **stale-owner and low-confidence warnings** on owner edges,
   **recently-corrected** warnings (an edge whose endpoint changed in a later version), **no-revisit**
   blocking, and **require-evidence** on `conclude`.
8. Decides whether the event is a **milestone** (see section 8).
9. Emits exactly **one event**. A refused call becomes a `blocked` event and is always a milestone.

**Repo limits** (`afd/repo.py`): one repo; the agent creates `agent/<task-id>-<run>` and opens a PR.
Writes to `main` raise `ProtectedBranchError` and become `blocked` events. This holds even if the
agent config allowed it, because the repo layer is independent of the config (defense in depth). With
real GitHub, also turn on **branch protection on `main`** as the backstop the spec asks for.

---

## 7. Human in the loop

`afd/channels.py`. There is one `ask_human` tool with swappable channels:

- **In-app (primary).** The question is stored and appears in every teammate's **inbox** addressed to
  that person. The agent's thread blocks on it. Access is flat, so anyone may answer on someone's behalf,
  and the event records **who actually typed it** (`answered_by`).
- **Gmail (mirror).** Slack was dropped in favor of Gmail. The same question is mirrored as an email
  to the person's address in the ontology. The default `AFD_GMAIL_MODE=dry_run` records exactly what
  would be sent (to, subject, body, message id) on the event and **sends nothing**. Real sending and
  reading replies back into a run are **not implemented** (see section 16).
- **Timeout and escalation** (decided here). Each stage sets `ask_timeout_s` (default 120 s) and
  `on_timeout`:
  - `escalate_to_second_source`: the agent asks the fresh second source. In the demo that is the person
    `responsible_for` the repo area the document lives in (CODEOWNERS).
  - `stop`, or the second ask also timing out: the run ends with status **`needs_human`** and a
    `human_timeout` flag.
- **Simulated teammates** (`AFD_AUTO_REPLY_S`) answer from personas for demos and the benchmark. Priya's
  security answer is plausible and specific but describes the **old** policy. It is not an obvious
  "that's not me", which would let the agent self-correct.

The run's events show the pause explicitly: an `ask_human` event with status `waiting`, then the reply as
an `observe` event with actor `human:<person_id>`.

---

## 8. Events, fidelity labels and narration

### One event per tool call or decision

Events are stored locally and mirrored to `<catalog>.<schema>.events`. Here is a real event from the demo:

```json
{
  "run_id": "r_8f2c1a", "seq": 10, "event_id": "r_8f2c1a:10",
  "timestamp": "2026-11-14T18:02:11+00:00", "stage": "investigate", "actor": "agent",
  "action_type": "ask_human", "tool": "ask_human",
  "node_ids": ["p_priya", "doc_security_specs"], "edge_ids": ["e_priya_owns_secspec"],
  "focus_node": "p_priya",
  "input": {"person_id": "p_priya", "question": "I'm building the invoicing backend. What do the security specs require ...?",
            "edge_ids": ["e_priya_owns_secspec"]},
  "result": {"question_id": "q_51e0a2c4", "to": "Priya Shah", "timeout_s": 120.0},
  "status": "waiting", "latency_ms": 3,
  "decision": null, "evidence_refs": [],
  "milestone": true,
  "plain_summary": "Asked Priya Shah, listed as owner of Security specs, and is waiting for a reply.",
  "narrator": "template", "config_version": 1, "ontology_version": 1,
  "external_ids": {"gmail_message_id": "dryrun-q_51e0a2c4"}, "warnings": [],
  "fidelity": {"input": "observed", "result": "observed", "plain_summary": "inferred"}
}
```

- `action_type` is one of `observe`, `traverse`, `query`, `decide`, `ask_human`, `act`, `conclude`, `blocked`.
- Decisions and citations are **data, not after-the-fact stories**. The agent must call
  `record_decision(options, chosen, rationale)` and `cite_evidence(claim, evidence_refs)`, and finish with
  `conclude(answer, claims[{text, evidence_refs}])`. Every tool result returns its `event_id`, so the
  agent can cite it.
- `external_ids` links the event to other systems: `github_pr`, `github_branch`, `gmail_message_id` and
  `genie_conversation_id`.
- **MLflow Tracing** supplies the low-level spans. With `AFD_MLFLOW=1` the run manager opens a span per
  run and per event, and stores the trace id on the run. Agent Bricks deployments trace by default.

### Fidelity labels

| Label | Covers | Where shown |
|---|---|---|
| **observed** | Tool inputs, returned data, human replies | Raw event, input and result panes |
| **reported** | `decision` (options, chosen, rationale) and the final answer | Decision cards, the Answer card |
| **inferred** | Narrator sentences, flags, proposals | The log, the flags panel, the proposals panel |

### Narration

`afd/narrator.py`:

- **Milestones only.** The wrapper decides what is a milestone, never a model. Milestones are stage
  starts, decisions, questions and replies, every write action (branch, file, PR, message), CSV and Genie
  queries, first visits to service, document, person, repo_area and task nodes, any warning, any blocked
  or error event, and the conclusion.
- **Input is the structured fields only.** Node and edge ids are resolved to labels. The agent's free text
  (its rationale, its question wording) is never passed to the narrator.
- **Output** is one sentence tied to its `event_id`. Clicking it in the log opens the raw event.
- **Grounding check.** Every name, file path and number in an LLM sentence must appear in the input.
  Otherwise the template sentence is used.
- **Fallback.** If the model is slow (`AFD_NARRATOR_TIMEOUT_S`, default 4 s) or down, the template sentence
  is used, so the log never stalls. `narrator` on each event records which one produced the sentence.

---

## 9. Post-mortem: flags, marking, proposals, rerun

### Automatic flags (`afd/flags.py`, all *inferred*)

| Flag | Rule |
|---|---|
| `stale_edge_used` | A decision, question or action **relied on** an edge older than the staleness limit (180 days, or a stricter configured limit). "Relied on" means the chosen option's edge, not edges merely looked at. |
| `low_confidence_edge` | The same, for an edge below the confidence floor (0.7). |
| `loop` | The same call repeated 3 times, the hop budget exhausted, or a revisit blocked by the no-revisit rule. |
| `dead_end` | A traversal or lookup came back empty. Medium severity when it was an owner lookup. |
| `unsupported_claim` | A claim or citation with no evidence. |
| `blocked_action` | The wrapper refused an action. |
| `human_timeout` | A question went unanswered. |

### Marking a run wrong

The teammate says what the right outcome was, for example *"It should have asked Marcus"*.
`localize_divergence` then finds the **first decision that chose someone else**. It also reports whether
the expected person was visible on the path ("chose wrong") or never appeared ("the ontology didn't lead
there"). The run is saved as a **test case**, and the reviewer is asked for proposals. That includes turning
the teammate's statement into an ontology fix, because it is a business fact only they can supply.

### Proposals (`afd/reviewer.py`)

- **Triggered only** on flagged runs, on runs marked wrong, or by **propose again**. Never on every run.
- Each proposal has `target` (`ontology` or `config`), the **exact ops** that accepting will apply, a
  **before/after diff**, the **step (`seq`) that caused it**, the label **AI-inferred**, and a limitations note.
- **Nothing applies automatically.** **Accept** applies the ops as a new version, attributed to whoever
  accepted. **Edit → accept** applies your edited ops. **Reject** records the decision. All three are audited.
- The **rules** proposer is the default and is deterministic: fixes that follow directly from flags. For
  example, a stale owner with a fresh second source produces *point the edge there*, plus *add the stale-owner
  rule*. A dead end produces *fall back to CODEOWNERS*, and a loop produces *no revisits*.
- The **llm** proposer (`AFD_REVIEWER=llm`, `databricks-gpt-oss-120b`) reads the milestone trace, flags, edges
  and rules, and returns ops in the same format. Invalid ops are dropped, and the deterministic proposals are
  always kept.
- **Honest limit, shown on every proposal:** the proposer only sees the trace and the graph. It shares the
  agent's blind spots, and it is weak at business facts it cannot see. The benchmark's wrong-mapping case
  demonstrates this.
- Fixing the agent's code or PR is **out of scope**. That happens in GitHub.

### Rerun and compare

**Rerun** starts the same task as the same requester against the **current** versions. Edges whose endpoint
was corrected since an earlier version carry a *Recently corrected* warning, so the live view warns about the
stale edge before the agent relies on the corrected one. **Compare** shows both runs' milestones side by side,
with versions, step counts, who was asked and the flags.

---

## 10. The UI

`static/` (vanilla JS + Cytoscape.js from jsDelivr; no build step). Header: **"You are"** picks the
teammate recorded as author. This is attribution, not permission.

- **Run.** Chat-style composer and run picker; the human-in-the-loop **inbox**; the narrated **log**
  (milestones, or all events). Each line has a stage chip, a fidelity chip and status/flag chips, and clicking
  it opens the raw event.
  - The **Answer** card: click a claim for the **evidence view** (supporting nodes and edges glow green, and the
    supporting events are listed).
  - The **graph**:
    - Fixed lanes, left to right: task → service → document/repo area → person → team.
    - Visited nodes have a ring, and the active node glows.
    - Traversed edges are blue, flagged edges are red and dashed, and stale edges (more than 180 days old) are
      always dotted.
    - Warned nodes are amber.
  - **Semantic zoom.** Zoomed out, nodes carry small **markers** (⑂ GitHub, ▦ CSV, ◍ web, ✉ message, ? ask,
    ◆ Genie, ⛔ blocked) with count badges. Zoomed in, each marker expands into **action nodes** (`#14 PR`) ordered
    by step and linked to the ontology node that triggered them. During a live run the **active node
    auto-expands**. Ontology node positions are locked, so expansion never moves them.
  - **How I got this:** a replay scrubber, step back and forward, and a ▶ replay of the milestones. Clicking a log
    line jumps the graph to that step. **live** returns to the latest step.
  - **Decision cards:** the options with the chosen one highlighted, and the rationale labeled *reported*, alongside
    the actor, nodes, edges, evidence, external ids and versions.
- **Review.** Automatic flags (click → jump to the step); **Mark wrong** with divergence localization; **Rerun**
  and **compare**; **Proposed fixes** with before/after diffs and accept, edit or reject buttons.
- **Editors.** The **rule editor** has a card per stage covering the goal, the rules (with optional structured
  check JSON), allowed tools, node and edge types, external app levels, and escalation and stop settings. The
  **ontology editor** is a table of edges with their provenance, with stale rows highlighted, plus a node table.
  Saving applies immediately as a new version.
- **History.** Ontology and rule versions with **one-click rollback**, the audit log (hover for before/after),
  saved test cases, and the rendered Agent Bricks instructions.
- **Benchmark.** Run it and view the results.

---

## 11. Benchmark

`afd/benchmark.py`, `scripts/benchmark.py`, and the Benchmark tab in the UI.

**Question:** does the ontology, and fixing it, make the agent more accurate?

| Condition | What the agent has |
|---|---|
| No ontology | Ontology tools removed. Only the team wiki page (last edited 2025-06). |
| Genie only | Ontology tools and the wiki removed. It asks the Genie space over the ontology views. |
| Our ontology | Graph walking with rules v1, as seeded. |
| Ontology after edit | Run once, **accept every AI proposal** from that run, then rerun. |

The cases cover each seeded failure from the spec plus controls: stale owner (the demo, as a build task and
as a question), conflicting sources (an old confident edge against a fresh, less confident one), missing edge
(only CODEOWNERS knows), loop (two services depending on each other), wrong mapping (a doc mapped to the wrong
repo area), and two controls. Metrics are accuracy, share of claims with evidence, and steps taken. Every case
is a real run through the tool wrapper, in an isolated store.

**Results** (committed in `docs/benchmark-results.json`, generated 2026-11-15 with a live Genie space):

| Case | No ontology | Genie only | Our ontology | Ontology after edit |
|---|---|---|---|---|
| demo (build task) | ✗ escalated | ✗ asked Dana | ✗ asked Priya | ✓ asked Marcus |
| stale owner | ✗ asked Priya | ✗ asked Priya | ✗ asked Priya | ✓ asked Marcus |
| conflicting sources | ✗ asked Kai | ✓ asked Priya | ✗ asked Kai | ✓ asked Priya |
| missing edge | ✓ asked Leo | ✗ escalated | ✗ escalated | ✓ asked Leo |
| loop | ✓ escalated | ✓ escalated | ✗ hop budget exhausted | ✓ escalated |
| wrong mapping | ✓ asked Sam | ✗ escalated | ✗ escalated | ✗ asked Leo |
| control: invoice reqs | ✓ | ✓ | ✓ | ✓ |
| control: auth service | ✓ | ✓ | ✓ | ✓ |
| **Accuracy** | **62%** | **50%** | **25%** | **88%** |
| Avg steps | 6.4 | 6.4 | 12.4 | 11.8 |

What it shows, honestly:

- An **unmaintained ontology is worse than nothing** on these cases. The cases are built around seeded
  failures, so this is adversarial by construction.
- **The fix loop is where the value is.** Accepting the AI's proposals takes the same graph from 25% to 88%.
- **Wrong mapping stays wrong after the edit.** The proposer cannot see that business fact. It "fixes" the
  dead end by trusting CODEOWNERS of the wrong area. This is the limit stated in spec section 11. The wiki
  happens to be right here.
- **Genie answers faithfully from the same stale tables.** It reports the stale owner with its provenance,
  but nothing in a plain data question tells it to doubt that owner. Genie answers are not deterministic
  across runs.
- Claims-with-evidence is 100% everywhere because the scripted host always cites. The metric matters for the
  LLM host.
- This harness measures the **system** (ontology, rules and fix loop) with a deterministic policy. It is
  **not** a measurement of an LLM's accuracy.

---

## 12. Databricks setup and deployment

All commands use your Databricks CLI profile (`databricks auth login`). They are idempotent where possible.

### 12.1 Unity Catalog tables (done for `workspace.agent_flow`)

```bash
.venv/bin/python scripts/setup_delta.py --warehouse-id <warehouse-id> --sync
```

This creates schema `workspace.agent_flow` with tables `ontology_versions`, `nodes`, `edges`,
`agent_config_versions`, `events`, `runs`, `questions`, `flags`, `proposals`, `test_cases` and `audit_log`.
**Every table and column has a comment** (Genie reads them). It also creates the views `nodes_current`,
`edges_current` (with labels and `age_days`) and `runs_latest`. `--sync` copies the local store in.

- All Delta tables are **append-only**. Mutable records are re-appended with a newer `updated_at`, and the
  `*_latest` view pattern reads the newest row per id.
- `nodes` and `edges` hold a full snapshot per `ontology_version`, so any past graph can be queried.

Run the app with `AFD_DELTA=1 AFD_WAREHOUSE_ID=<id>` and every write is **mirrored continuously** (a
background queue with batched, parameterized INSERTs; mirror failures never break the app). When the local
store is empty (a fresh Databricks App container), the app **rebuilds itself from Delta** on startup
(`hydrate_from_delta`).

### 12.2 Genie space (done: "Agent Flow Debugger ontology")

```bash
.venv/bin/python scripts/setup_genie.py --warehouse-id <warehouse-id>
export AFD_GENIE_SPACE_ID=<printed id>
```

This creates a Genie space over `edges_current` and `nodes_current` with instructions and sample questions.
The agent's `genie_query` tool and the benchmark's Genie condition use it. The instructions describe the
tables and provenance but deliberately do **not** encode our agent's rules, so it stays a plain baseline.

### 12.3 Deploy the app (not done; run when you're ready)

`app.yaml` runs `python -m afd` on `DATABRICKS_APP_PORT`, with Delta on and a SQL warehouse bound as the app
resource `sql-warehouse`.

```bash
databricks apps create agent-flow-debugger
databricks sync . /Workspace/Users/<you>/agent-flow-debugger --exclude .venv --exclude data --exclude agent_bricks
databricks apps deploy agent-flow-debugger --source-code-path /Workspace/Users/<you>/agent-flow-debugger
```

Then, in the app's settings, add the SQL warehouse resource named `sql-warehouse` (CAN USE). Grant the app's
service principal `USE SCHEMA` and `SELECT`/`MODIFY` on `workspace.agent_flow`, `CAN QUERY` on the narrator
and reviewer serving endpoints, and access to the Genie space if you set `AFD_GENIE_SPACE_ID`.

### 12.4 Deploy the Agent Bricks agent (not done; run when you're ready)1

```bash
cd agent_bricks
agentbricks login --profile <profile>
agentbricks dev                         # local: http://localhost:8000 (set AFD_APP_URL to your app)
agentbricks deploy                      # deploys as agent-bricks-afd-agent (~3 min first time)
```

- Set `AFD_APP_URL` in `agent_bricks/app.yaml` (`env:`) to the debugger app's URL.
- Set the debugger's `AFD_AGENTBRICKS_URL` to the agent's URL, and `AFD_AGENT_HOST=agentbricks` (or pick the
  host per run).
- The two apps call each other with Databricks OAuth tokens. Each one's service principal needs **CAN USE** on
  the other app.

---

## 13. Configuration reference

| Variable | Default | Meaning |
|---|---|---|
| `AFD_DATA_DIR` | `data` | Local working store, simulated repo, benchmark output. |
| `AFD_AGENT_HOST` | `scripted` | `scripted` or `agentbricks`. |
| `AFD_AGENTBRICKS_URL` | `http://127.0.0.1:8000` | Where the Agent Bricks agent is served. |
| `AFD_AGENTBRICKS_TIMEOUT_S` | `1200` | Max time for one agent invocation (includes waiting on humans). |
| `AFD_NARRATOR` / `AFD_NARRATOR_ENDPOINT` | `template` / `databricks-meta-llama-3-1-8b-instruct` | `llm` turns on model narration (grounding-checked). |
| `AFD_NARRATOR_TIMEOUT_S` | `4` | After this, use the template sentence. |
| `AFD_REVIEWER` / `AFD_REVIEWER_ENDPOINT` | `rules` / `databricks-gpt-oss-120b` | `llm` adds model-proposed fixes. |
| `AFD_DELTA`, `AFD_CATALOG`, `AFD_SCHEMA`, `AFD_WAREHOUSE_ID` | off, `workspace`, `agent_flow`, — | Delta mirror and rebuild. |
| `AFD_GENIE_SPACE_ID` | — | Turns on `genie_query` and the Genie benchmark condition. |
| `AFD_GITHUB_MODE`, `AFD_GITHUB_REPO`, `GITHUB_TOKEN` | `sim` | `rest` uses the real GitHub API (unverified). |
| `AFD_GMAIL_MODE` | `dry_run` | `send` is not wired in this build (it logs a warning and stays in dry-run). |
| `AFD_AUTO_REPLY_S` | `-1` | ≥0: simulated teammates answer after N seconds. |
| `AFD_TODAY` | real date | Pin "today" for staleness math. |
| `AFD_MLFLOW` | `0` | `1`: MLflow spans per run and per event. |
| Agent side: `AFD_APP_URL`, `AFD_AGENT_MODEL`, `AFD_TOOL_TIMEOUT_S`, `AFD_MAX_TURNS`, `AFD_MODEL_MAX_RETRIES` | `http://127.0.0.1:8765`, `databricks-qwen3-next-80b-a3b-instruct`, `900`, `60`, `8` | Set in `agent_bricks/`. |

---

## 14. HTTP API

| Method & path | Purpose |
|---|---|
| `GET /api/meta` | People, types, tools, versions, modes, today. |
| `GET /api/ontology?version=` · `GET /api/ontology/versions` | Snapshot (latest or a given version) · history. |
| `POST /api/ontology/ops` `{ops, author, note}` | Direct edit → new version. |
| `POST /api/ontology/rollback` `{version, author}` | Roll back → new version. |
| `GET /api/config?version=` · `GET /api/config/versions` · `GET /api/config/instructions` | Agent config · history · rendered Markdown. |
| `PUT /api/config` `{config, author, note}` · `POST /api/config/rollback` | Rule editor save · roll back. |
| `GET /api/audit` | Audit log (newest first). |
| `POST /api/runs` `{task, requester, host?}` · `GET /api/runs` · `GET /api/runs/{id}` | Start · list · run + events + flags + proposals + questions. |
| `GET /api/runs/{id}/stream?after=` | **Server-sent events**: `ev` (each event), `state` (run + open questions), `done` (flags + proposals). |
| `POST /api/runs/{id}/mark` `{expected_outcome, author}` | Mark wrong → divergence + test case + proposals. |
| `POST /api/runs/{id}/review` · `POST /api/runs/{id}/rerun` · `GET /api/compare?a=&b=` | Propose again · rerun · side-by-side. |
| `GET /api/questions?status=open` · `POST /api/questions/{id}/reply` `{text, author}` | Inbox · reply (unblocks the agent). |
| `POST /api/proposals/{id}/accept` `{author, ops?}` · `POST /api/proposals/{id}/reject` | Decide a proposal (`ops` = edited). |
| `GET /api/repo?branch=` · `GET /api/testcases` | Simulated repo (branches, PRs, files) · saved test cases. |
| `GET /api/benchmark` · `POST /api/benchmark/run` | Results · run in the background. |
| `GET /api/agent-tools` · `POST /api/agent-tools/{tool}` `{run_id, ...args}` | Tool catalogue · **Agent Bricks tool bridge**. |

---

## 15. Tests

```bash
.venv/bin/python -m pytest -q       # 22 tests, < 2 s, fully offline
(cd agent_bricks && ../.venv/bin/python -m pytest -q tests)   # the agent's own 3 tests
```

- `tests/test_data_layer.py`: the store, ontology versioning, rollback and validation, config ops and
  validation, instruction rendering, repo protection.
- `tests/test_agent_flow.py`: the whole demo.
  - The first run asks Priya and opens a PR built on HS256.
  - The stale flag fires, and exactly two proposals (ontology and rules) are created with nothing auto-applied.
  - After accepting both, the rerun asks Marcus with a *Recently corrected* warning and RS256.
  - A rule-only rerun goes through the second source with a *Stale owner* warning.
  - Edit-then-accept works.
  - Mark-wrong localizes the divergence.
  - All five kinds of blocked action are emitted as events.
  - A timeout escalates to the second source and then to `needs_human`.
  - An in-app reply unblocks the agent, with attribution.
  - The narrator's grounding rejects invented names and numbers, and the agent's free text never reaches it.
- `tests/test_benchmark.py`: the benchmark's expected pattern, including the honest wrong-mapping miss.
- `tests/test_agentbricks_bridge.py`: a fake agent drives the Agent Bricks host through the tool bridge.
  Calls land in the run, a disallowed call is blocked, a turn that ends without `conclude` gets exactly one
  continuation, and the agent's end-of-turn messages are recorded.

`agent_bricks/tests/` holds the agent's own tests: the HTTP contract, including `run_id` pass-through.

---

## 16. Decisions, verification status and limits

### Spec "open" items: what this build decided

| Open item | Decision |
|---|---|
| Models | Agent: `databricks-qwen3-next-80b-a3b-instruct` via the Agent Bricks agent. It completed the demo; `databricks-gpt-oss-120b` tended to wander and skip stages. Narrator: `databricks-meta-llama-3-1-8b-instruct`. Reviewer: `databricks-gpt-oss-120b`. All are Databricks-hosted, with no external key. A deterministic fallback exists for each. For heavy use, put the agent on a **provisioned-throughput** endpoint: the pay-per-token endpoints have low workspace QPS limits that a tool-calling loop can hit (`429 REQUEST_LIMIT_EXCEEDED`). The agent retries with backoff, and the host continues after a failed turn. |
| Event streaming | Server-sent events from the app's store; Delta receives every event through the write-behind mirror. This was chosen over polling Delta (latency) and Lakebase (extra infrastructure). |
| Ask timeout and escalation | Per-stage `ask_timeout_s` (120 s). On timeout, escalate to the fresh second source, then stop with `needs_human`. |
| Rerun and compare | Both are in the demo. |
| UI layout | As in section 10. |
| Benchmark design | As in section 11. |

### User decisions during the build

- The agent is hosted with **Agent Bricks**: the Agent Bricks CLI, because Supervisor Agents are now legacy.
- **Slack was dropped; Gmail is the only external channel.**

### Verification status

| Item | Status |
|---|---|
| Offline demo, flags, proposals, rerun, compare, editors, history (UI checked in headless Chrome) | ✅ verified |
| Delta tables, comments and views in `workspace.agent_flow`; live mirror (a demo run's 28 events landed); rebuild from Delta | ✅ verified against the workspace |
| Genie space creation and the `genie_query` tool; Genie benchmark condition | ✅ verified against the workspace |
| Hosted model endpoints exist (gpt-oss, Llama, Qwen, Gemma) | ✅ verified (`serving-endpoints list`) |
| Agent Bricks agent (`agent_bricks/`, run locally) driving the full demo through the wrapper with a real model | ✅ verified with Qwen3 Next 80B. The first run walked task → service → Security specs → **asked Priya**, built HS256 code, opened PR #1, concluded with 5/5 evidence-backed claims; the post-mortem flagged the stale edge and proposed both fixes. After accepting them, the LLM rerun showed the *Recently corrected* warning, **asked Marcus**, and wrote RS256 code in PR #2. That rerun then hit a workspace QPS limit (429) during verify, which led to the retry and continuation hardening (unit-tested). |
| Deployed Databricks App and deployed Agent Bricks agent; app-to-app OAuth | ⚠️ not deployed in this build; written to the documented pattern |
| Real GitHub (`AFD_GITHUB_MODE=rest`) | ⚠️ unverified |
| Gmail sending; Gmail reply reading | ❌ not implemented (dry-run only) |
| Apps acting as the signed-in user (user authorization) | not used; `X-Forwarded-Email` is only recorded for attribution |
| Genie Ontology (preview) | not used |
| MLflow spans from the app (`AFD_MLFLOW=1`) | ⚠️ code path present, not exercised in tests |

### Limits to state plainly

- The system makes actions and **stated** reasoning auditable, not the model's internals. Fidelity labels are
  kept throughout.
- The proposer shares the agent's blind spots. It is weak at business facts, as the wrong-mapping case shows.
- The scripted host is deterministic by design. LLM-host behavior varies run to run, which is exactly what the
  trace and flags are for.
- The local store is a single-process working cache. Delta is the system of record across restarts.

---

## 17. Repository layout

```
afd/                 the app (see the module map in section 3)
agent_bricks/        the LLM agent (Agent Bricks CLI scaffold: agent/agent.py, runtime/main.py, app.yaml, agent.toml)
static/              UI (index.html, style.css, app.js)
sql/schema.sql       Unity Catalog DDL + views
scripts/             setup_delta.py, setup_genie.py, benchmark.py
tests/               pytest suite
docs/                benchmark-results.json
app.yaml             Databricks Apps manifest for the debugger
requirements*.txt    runtime / dev dependencies
```
