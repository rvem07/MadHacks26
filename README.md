# Agent Watcher — WildGuard Labs / Sentinel

Tier 1 incident investigation and Tier 2 cited build plans for a fictional conservation company with exactly ten people. Extends the existing FastAPI app, Cytoscape.js graph, checkpoint store, SQL persistence, and Databricks deployment bundle. Earlier BadgerNet and guided wildlife implementations are retained; their historical instructions are in [docs/legacy-demo.md](docs/legacy-demo.md).

Deployed App: **https://agent-watcher-7474649299876234.aws.databricksapps.com** (Databricks authentication required).

## Tier 2: cited build plans

New runs automatically synthesize a structured Sentinel application build plan after the Tier 1 persona answers and excerpt summary are saved. Both **Start run** and **Run all** use this path. The **Plan** panel displays the canonical Markdown, findings link to their source people on the graph, tasks are grouped by owner, and **Download plan.md** downloads the same server-generated Markdown. Task owners glow from persisted `synthesis` events. Existing Tier 1 runs and data are retained; historical completed runs are not silently regenerated.

The plan JSON has `summary` (exactly three sentences), `findings` (`finding`, `source_person_email`), `security_requirements`, 6–8 `tasks` (`title`, `description`, `owner_email`, `topic`), `risks`, and `open_questions`. Schema validation uses Pydantic. Summary sentences and findings must be exact excerpts from collected answers, so these factual fields cannot acquire generated facts. An overlong summary made entirely of source sentences is shortened to three; other schema errors get one bounded repair attempt. Task descriptions, security requirements, and risks also quote source constraints directly; task titles describe proposed workflow features. A separate temperature-0 audit checks the plan against answers and ontology. This audit is an LLM check, not a formal proof; explicit source and owner checks remain deterministic.

The model proposes task topics, never authoritative owners. Each distinct canonical topic is resolved with `who_should_i_ask`; repeated topics reuse that verified route. Code overwrites any model-supplied owner with the ontology owner, validates the person against the pinned Gold nodes and review flags, and logs corrections. Unsafe/unowned topics are flagged with an execution error; no invalid plan is published. Multiple distinct implementation tasks may belong to one topic/owner. Nadia's context can inform work, but her CTO title alone does not create a responsibility assignment.

Unknown facts remain questions. Questions answered explicitly with “I don't know from my context” are always retained by code; source statements explicitly marking unresolved facts are also preserved under open questions; the model also lists unresolved facts in source notes, such as sighting age, viewer identities, coordinate copying, and incident-specific CEO approval. The build plan describes proposed software workflows, not completed implementations or permission to disclose locations.

Plans are physical managed Delta records in **`workspace.sentinel_gold.plans`**, with `run_id`, `plan_json`, `created_at`, and internal projection key/provenance columns. The plan is saved in the atomic checkpoint and projected before the `synthesis` event is exposed. Projection recovery repairs it idempotently. A failed synthesis can be retried without asking personas again. The UI plan API and download read the physical Delta table in deployed mode:

```text
GET /api/watcher/runs/{run_id}/plan
GET /api/watcher/runs/{run_id}/plan.md
```

For an existing Tier 1 deployment, the deployment helper's **`--skip-pipeline`** path creates only the new plans table and redeploys code; it does not ingest another batch or alter existing graph tables:

```bash
.venv/bin/python scripts/deploy_sentinel.py --warehouse-id YOUR_WAREHOUSE_ID --skip-pipeline
.venv/bin/python scripts/verify_synthesis_model.py --warehouse-id YOUR_WAREHOUSE_ID
.venv/bin/python scripts/verify_tier2_browser.py --warehouse-id YOUR_WAREHOUSE_ID
```

The deployed verifier starts a real Run all and adds the unanswered question “identify the fourteen external viewers by name.” It checks 6–8 safe ontology owners, every finding/source link, graph highlights, open questions, Markdown download, physical Delta equality, and attempts Catalog Explorer itself. It reports interactive SSO separately if the workspace UI does not accept the verifier's OAuth session.

```sql
SELECT run_id, created_at, plan_json
FROM workspace.sentinel_gold.plans
ORDER BY created_at DESC;
```

Catalog Explorer: [workspace → sentinel_gold → plans](https://dbc-4702f9e0-0c88.cloud.databricks.com/explore/data/workspace/sentinel_gold/plans). In the Columns tab inspect the schema, then use Sample Data or the SQL query above to inspect stored JSON. Normal UC permissions and workspace authentication apply.

Development uses `.worktrees/sentinel-tier2` with the existing interpreter/dependencies shared from the main checkout. Worktrees isolate Git changes only; no worktree path or alternative store is used by the deployed app. Tier 2 results: [docs/tier2-verification.md](docs/tier2-verification.md).

## Stack and layout

- `afd/main.py`, `afd/watcher_api.py`: FastAPI and event-driven SSE.
- `afd/watcher.py`, `afd/sentinel.py`, `afd/synthesis.py`: existing workflow host plus Sentinel routing, personas, concurrent questions, manual waiting, resume, and validated build-plan synthesis.
- `afd/pipeline.py`, `afd/watchstore.py`: bronze/silver/gold transforms, Unity Catalog Delta tables, atomic checkpoints and physical projections.
- `static/sentinel.*`: Cytoscape UI using existing helpers and graph styles; no React/build step.
- `data/org_chart.csv`, `data/responsibilities.csv`, `data/person_context.csv`: checked-in fictional inputs; no secrets or real animal coordinates.
- `scripts/run_pipeline.py`: Databricks serverless Job entry point; `databricks.yml` provisions the Job and App; `app.yaml` configures the App.

Sentinel runtime requires `AFD_DELTA=1`, a Databricks SQL warehouse, and `AFD_WATCHER_AGENT=databricks`. It refuses local storage/model fallbacks. Its local JSON/model doubles exist only in explicit tests. CSVs are pipeline inputs, never runtime UI/persona data sources. The App reads published graph, context, routing and statistics from Unity Catalog tables.

## Deploy and run the pipeline

Use Python 3.11+, the Databricks CLI, an authenticated workspace profile, an existing accessible SQL warehouse, and a queryable chat model serving endpoint. The deployer needs permission to create Apps/Jobs/schema/tables and grant the App principal access. Serverless Jobs and Apps must be enabled.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
# Authentication stays in the external CLI/SDK credential store.
databricks auth login --host https://YOUR-WORKSPACE
.venv/bin/python scripts/deploy_sentinel.py --warehouse-id YOUR_WAREHOUSE_ID
```

The helper validates and deploys the existing bundle, executes the ingestion Job **on Databricks**, grants the App principal `USE CATALOG`, `USE SCHEMA`, `SELECT`, and `MODIFY`, starts the App, and prints its URL. Omit `--warehouse-id` to select an accessible running warehouse, then prefer serverless among remaining candidates. Use `--profile NAME` or `--model-endpoint NAME` as needed. For a code-only redeploy against an already published schema, add `--skip-pipeline`. The default model is `databricks-qwen3-next-80b-a3b-instruct`; endpoint existence is checked before deployment.

Default catalog/schema: **`workspace.sentinel_gold`**. This isolated schema holds logical bronze, silver, gold and workflow tables without altering old `workspace.agent_watcher` data. To use another catalog/schema, change both `app.yaml` and bundle defaults, and adapt the grant target in the deployment helper. The app service principal gets warehouse `CAN_USE` and serving endpoint `CAN_QUERY` through declared App resources. No credentials are checked in.

To rerun ingestion after deployment:

```bash
databricks bundle run watcher_pipeline --var warehouse_id=YOUR_WAREHOUSE_ID
```

To validate and deploy changes manually:

```bash
databricks bundle validate --var warehouse_id=YOUR_WAREHOUSE_ID
databricks bundle deploy --var warehouse_id=YOUR_WAREHOUSE_ID
databricks bundle run watcher --var warehouse_id=YOUR_WAREHOUSE_ID
```

For projection repair without re-ingesting CSVs, run the Job with:

```bash
databricks bundle run watcher_pipeline --var warehouse_id=YOUR_WAREHOUSE_ID --python-params '["--sentinel","--delta","--warehouse-id","YOUR_WAREHOUSE_ID","--catalog","workspace","--schema","sentinel_gold","--repair"]'
```

Keep exactly one App process/replica (the bundle sets min/max replicas to one). Checkpoints use compare-and-swap revisions; Unity Catalog keys are informational. This is a single-writer demo, not a distributed task queue.

## Data and quality semantics

Bronze tables (`bronze_org_chart`, `bronze_responsibilities`, `bronze_person_context`) preserve input values, source file/line, ingestion timestamp, and batch. MERGE is insert-only. Every successful ingestion appends a new batch; retries/repair of one batch are idempotent. `active_pipeline` changes only after all gold projections succeed; existing runs pin their batch.

Silver trims/lowercases emails. Exact normalized names merge deterministically; name-prefix matches also require the same title and normalized manager. The fullest name wins, then source order. This resolves `Mei T.` / `mtanaka@...` to Mei Tanaka without an external alias file. Conflicting manager assertions are retained and quarantined. `data breach` canonicalizes to `security incident`.

**Count assumptions:** 12 people rows describe 10 people. One extra row is Mei’s identity alias; the other is Jamal’s contradictory reporting assertion. Thus `duplicates_merged=1` counts identity merges, not rejected reporting assertions. “4 quarantined” means four issue groups, covering **six retained records**: Jamal’s two manager rows, the two cycle members, Priya’s missing-manager row, and the unowned topic. `violations_quarantined=6` reports physical records and `quarantined_count=4` reports issue groups. The UI displays both. This distinction preserves every planted problem.

Gold `nodes` exposes `id, type, name, properties, source, confidence, updated_at` plus batch and complete JSON provenance. It has ten person nodes, nine canonical topic nodes, and four team nodes. `edges` has `reports_to`, `member_of`, `responsible_for`. Person IDs are normalized emails. Invalid reporting edges are excluded; people remain visible with `properties.needs_review=true`.

Jamal, Hannah, Sofia, and Priya are excluded from agent routing, even though their responsibilities remain visible. The partner NGO agreements topic remains visible without an owner. Demo people Ben, Mei, Carlos, Elena and Nadia are unaffected. `person_context` contains 25 short factual notes (2–3 per person). Tables/columns have comments; `pipeline_stats`, `quarantine`, current graph views and `topic_routing` support inspection.

```sql
SELECT raw_people, unique_people, duplicates_merged, quarantined_count,
       violations_quarantined
FROM workspace.sentinel_gold.pipeline_stats
WHERE batch_id = get_json_object(
  (SELECT record FROM workspace.sentinel_gold.workflow_documents
   WHERE id = 'active_pipeline'), '$.batch_id');
-- Expected: 12, 10, 1, 4, 6

SELECT reason, group_id, record
FROM workspace.sentinel_gold.quarantine
ORDER BY batch_id, reason, group_id;

SELECT topic, person_email, depth
FROM workspace.sentinel_gold.topic_routing
WHERE topic = 'security incident'
ORDER BY depth;
-- Ben -> Nadia -> Elena
```

## Demo

1. Open the App URL and sign in with an authorized Databricks identity.
2. Leave manual toggles off. Click **Start run** with the prefilled black-rhino exposure task. The agent calls `who_should_i_ask("security incident")`, saves a pending question for Ben and `waiting_on_human`, then his context persona answers and the run resumes. It continues to Mei, Carlos, and Elena and produces a short summary.
3. Click **Reset**, then **Run all**. Five pending questions are committed before concurrent model calls for Ben, Mei, Carlos, Elena and Nadia. Multiple waiting nodes glow together. Answers arrive independently and the final summary includes the collected context.
4. Reset. Enable **Manual mode for Elena Marsh** before starting. Elena’s request waits until you type an answer. Refreshing preserves the exact pending request. Submit to resume and finish. Manual preferences persist; each run snapshots them at start. Toggles are disabled while the run is active.
5. Reset cancels pending work and clears the current browser selection; it does not delete Delta history. It does not cancel an already issued model invocation, but its result cannot change a cancelled run.

The bounded Tier 1 plan asks incident-context questions; changing the prompt changes the investigation summary, not the role sequence. Run all adds Nadia because the deployment/rollback context is relevant. This is a demonstration of context collection and approval policy, not actual incident approval or partner notification.

Routing uses the Databricks-served model at **temperature 0**, constrained to routing-safe canonical topics, then reads owner and validated escalation chain from Gold. Personas receive only title, their factual notes, and a safe directory for referrals. The model selects relevant source-note indices; code validates the indices and renders only those source facts. It cannot generate extra persona facts. An out-of-context question returns “I don't know from my context” and names a directory person who might know. The short final synthesis uses a separate temperature-0 served-model call to select one or two source sentences from each collected answer. Request and sentence references are validated before rendering. No free-form generated factual claims are accepted in the summary; policy alone must never be treated as actual approval.

For an out-of-context check, POST `/api/watcher/persona` with:

```json
{"person":"ben.okafor@wildguardlabs.org","answer":"What is the cafeteria menu today?"}
```

`workflow_documents` stores the atomic run checkpoint, pending requests and event outbox. `pending_requests`, `runs`, and `events` are replayable physical Delta projections. States: `running`, `waiting_on_human`, `resumed`, `done`, `cancelled`. Each pending request records request/run/person/question/status/answer/created/answered fields. Each event records run/step/actor/action/target/edge/summary/state/timestamp.

The UI reads graph activity, question cards, step list, persona answers, and summary exclusively from the **physical events table**, via `/events` and SSE. There is no hardcoded animation sequence. Successful commits notify SSE subscribers; manual waiting has no polling loop or timer. SQL statement completion uses transport-level waits only. App restart repairs projections and resumes pending automatic work; manual questions remain waiting. Failed execution is explicit and can be retried with `POST /api/watcher/runs/{id}/retry`; answered requests are not repeated.

## Verification

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/validate_bundle.py
.venv/bin/python scripts/verify_sentinel_delta.py --warehouse-id YOUR_WAREHOUSE_ID
node --check static/sentinel.js
.venv/bin/pip install -r requirements-browser.txt
# Requires Chrome/Chromium and permission to bind a temporary local socket.
.venv/bin/python -m pytest tests/test_sentinel_browser.py --run-sentinel-browser -q
# Actual deployed acceptance, new private browser context with interactive SSO:
.venv/bin/python scripts/verify_sentinel_browser.py --url YOUR_APP_URL --login
# Or authenticate the fresh context with an external CLI OAuth token:
.venv/bin/python scripts/verify_sentinel_browser.py --url YOUR_APP_URL --oauth
```

The fixture browser test explicitly injects a model and temporary test document store; its UI badge says **TEST FIXTURE**, and it is not evidence of live Databricks acceptance. The deployed verifier injects neither and checks counts, sequential flow, concurrent glow, Elena manual waiting plus refresh, and out-of-context handling. It prints a JSON report and saves an ignored screenshot.

**Incognito assumption:** a fresh private context must authenticate through Databricks SSO with `CAN_USE` access. Databricks Apps do not support anonymous/public access. No browser cookies or login state are committed.

Current verification results and deployment URL are recorded in [docs/sentinel-verification.md](docs/sentinel-verification.md).

## Databricks references checked

- [App deployment](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/deploy)
- [App resources and permissions](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/resources)
- [App service principal authorization](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/auth)
- [App access and the restriction on anonymous use](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/permissions)
- [Model-serving query API and temperature](https://docs.databricks.com/api/model-serving-query/v1)
- [Bundle resource schema](https://docs.databricks.com/aws/en/dev-tools/bundles/resources)

Remaining assumptions: an accessible chat endpoint supports JSON instruction following; only one App writer is active; no network/proxy buffering prevents SSE; CDN access is available for Cytoscape. These are verified where possible during deployment and browser checks, rather than replaced by a local runtime fallback.
