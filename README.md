# Fernwood Agent Watcher

Deployed App: https://agent-watcher-7474649299876234.aws.databricksapps.com (Databricks authentication required).

Acceptance results: [docs/verification.md](docs/verification.md).

A Databricks hackathon demo: a fictional ten-person company becomes a people graph, and a live agent consults simulated employees before generating a tiny time-off tracker. The presenter sees every question, answer, real search, test result, and commit as persisted events.

## Architecture

Exact CSV inputs in `data/` → Unity Catalog volume → append-only Bronze → deterministic Silver identity/relationship rules → Gold people and relationships → Databricks-served model → Delta run context/events → FastAPI + Cytoscape UI in a Databricks App.

The existing App entry point, SQL transport, compare-and-swap checkpoints, batched Delta projections, pipeline Job, and graph library are retained. Runtime has no SQLite, local JSON, CSV, or simulated Git fallback. Test doubles exist only in `tests/`. Generated target-app files may use temporary storage for testing; they are build artifacts, not the Watcher's database.

- `afd/pipeline.py`: exact ingestion and deterministic identity/relationship resolution.
- `afd/watchstore.py`: Delta schemas, checkpoint/outbox recovery, physical table reads.
- `afd/watcher.py`: bounded model planning, extractive personas, caching, deliberate dwell.
- `afd/research.py`: Tavily retrieval and paraphrased citations; unavailable is explicit.
- `afd/build.py`: model-generated TypeScript, source validation, real Node tests.
- `reference_app/`: a small working TypeScript scaffold supplied to the served model; the model adapts it to cited requirements. Required UI sections are validated so a form-only stub cannot pass as a complete build.
- `afd/preflight.py`: checks performed by the deployed App principal.
- `static/`: people graph, pipeline Data view, Setup, timeline, replay, source preview.
- `scripts/run_pipeline.py`: Databricks Job and volume upload/readback.

## Configuration and deployment

Use the existing authenticated Databricks CLI/SDK credential store. Never place credentials in files or command arguments.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
databricks auth login --host https://YOUR-WORKSPACE
# Confirm the warehouse and served model in databricks.yml.
databricks bundle validate --var warehouse_id=YOUR_WAREHOUSE_ID
databricks bundle deploy --auto-approve --var warehouse_id=YOUR_WAREHOUSE_ID
databricks bundle run watcher_pipeline --var warehouse_id=YOUR_WAREHOUSE_ID
# Grant the App service principal USE CATALOG, USE SCHEMA, SELECT, MODIFY
# on the fernwood schema, then start the App.
databricks bundle run watcher --var warehouse_id=YOUR_WAREHOUSE_ID
```

`app.yaml` references warehouse and model resources; the App uses its own service principal. The default physical namespace is `workspace.fernwood`; layer names are table prefixes in this namespace. Inputs are uploaded byte-for-byte to `/Volumes/workspace/fernwood/inputs`. Each pipeline run appends a batch, then activates it only when every projection succeeds. Existing runs pin their batch. Gold current views support Catalog inspection. Keys are informational, and the App uses a single process/replica.

Tables include `raw_files` (exact volume file snapshots and hashes), three `bronze_*` tables, `silver_people`, `silver_relationships`, `quarantine`, `nodes`, `edges`, `pipeline_stats`, `workflow_documents`, `runs`, `events`, `run_context`, `persona_cache`, `research`, and `artifacts`. The Data view reads these real tables, including exact raw file snapshots; it never reads local CSVs.

## Git destination and preflight

Configured destination found in the workspace:

- `GIT_FOLDER_PATH=/Workspace/Users/kaashish.v@gmail.com/.bundle/Test-Repo`
- `GITHUB_REPO_URL=https://github.com/Kman771/Test-Repo`

The destination is a sibling of the Watcher's bundle folder, not nested inside its Git repository. Current preflight evidence is in `docs/preflight.json`. The App can read the folder and reach `api.github.com`. Workspace API write returned `RESOURCE_DOES_NOT_EXIST` for the existing parent folder. This is not proof of a permission problem: the folder has Git CLI storage, and API/compute support must be checked separately. Standard Repos APIs provide no commit/push operation.

The Databricks serverless Git CLI probe created a local commit but GitHub rejected push with HTTP 403. Cleanup removed the probe file and restored main. See `docs/git-preflight-result.json`. The selected fallback is GitHub REST API, pending its repo-scoped token. No remote commits are claimed; only main exists on GitHub after the failed probe. The Setup panel reports the selected path and offers a read-only connection check. If direct Git cannot work, use a fine-grained GitHub PAT restricted to **Kman771/Test-Repo**, Contents read/write, as a Databricks secret with key `GITHUB_TOKEN`, referenced through an App secret resource. The bundle binds the `fernwood/GITHUB_TOKEN` secret to the App through its `github-token` resource.

## Search setup

Provider: Tavily. Create a dedicated Databricks secret scope (for example `fernwood`) and store the provider key as `TAVILY_API_KEY`. Add that secret as an App resource named `search-key` with read access, and add to `app.yaml`:

```yaml
- name: TAVILY_API_KEY
  valueFrom: search-key
```

Create keys interactively (values are not command arguments):

```sh
databricks secrets create-scope fernwood
databricks secrets put-secret fernwood TAVILY_API_KEY
databricks secrets put-secret fernwood GITHUB_TOKEN
```

After the keys exist, add these entries to `resources.apps.watcher.resources` in `databricks.yml`:

```yaml
- name: search-key
  secret:
    scope: fernwood
    key: TAVILY_API_KEY
    permission: READ
- name: github-token
  secret:
    scope: fernwood
    key: GITHUB_TOKEN
    permission: READ
```

`app.yaml` already references `GITHUB_TOKEN` through `valueFrom: github-token`, and the bundle declares that secret resource. After replacing the stored token, redeploy the App to load its new value. The first real publish verifies GitHub API create/delete commits on a separate preflight branch before creating the app's branch. Setup's Test connection is read-only; `POST /api/setup/preflight` explicitly runs the write probe.

The bundle must declare the matching secret resources, or add them through the App resource UI. Do not put the value in YAML. No search key is configured in this deployment yet. Search emits visible `search_unavailable` events until configured; no URLs or search results are fabricated. Queries exclude employee names, emails, and company name. Only URLs returned with retrieved source text can be cited; a temperature-zero model paraphrases short key points.

## Demo

The web server starts before saved-run recovery; history restoration runs in the background with bounded retries and reports its status through `/health`. Hover cards leave node colors unchanged. The green pulse indicates the agent’s current location, including time spent waiting for the next step. Direct travel trails connect visited nodes regardless of team relationships. Web search (purple), Coding (blue), and GitHub (amber) are separate workflow nodes; unavailable search is labeled and does not block building. Temporary transport failures retry up to twice from saved checkpoints.

1. Open the deployed App with an authorized Databricks identity. A fresh incognito window still requires authentication; Apps are not anonymous websites.
2. Click **Data**, inspect Raw CSV → Bronze → Silver → Gold, including quarantine, then click **Graph**.
3. Hover or click people for title, manager, expertise, and review notes. The accessible person selector offers the same information.
4. Leave the default time-off prompt, choose **Dry run** to preview source, and click **Run**. Disable dry run only when Setup reports a verified Git writer.
5. Watch the token visit employees. Every persona answer is labeled **simulated**, with **cached** shown when reused. Fresh answers bypass the cache.
6. Inspect cited requirements, search links/unavailability, actual test output, generated source or real commit links.
7. **Replay last run** reads stored Delta events with an explicit REPLAY badge; it never invokes the model, search, or Git writer.

## Data and interpretation choices

Expected counts: 12 raw people → 10 people, one fuzzy duplicate merge, one exact-email collapse, two quarantined relationship groups, and eight valid collaborations. Name-prefix matching compares first names plus exact surname, with normalized team, manager and title (`Sr.` → `Senior`). Marcus's fuller name wins. The canonical email repoints Marc's collaboration. Context emails are used as-is.

Jordan has one quarantined conflict with two candidate manager edges. Nina has one quarantined edge to a missing ID. Thus there are **two quarantine records and three retained candidate edges**. Gold contains exactly ten person nodes. The UI renders Nina's missing endpoint as a labeled dashed line stub, not an invented eleventh person. Review flags do not block asking these people: their context is valid even when their reporting edge needs review.

The planner enforces coverage in this order: policy/approvals (two people), payroll, tech standards, data model, frontend/calendar, design, coverage/blackouts (two people). It chooses the next eligible person and question at temperature zero, preferring adjacent people, with at most nine asks and no repeats. This leaves one of the ten people unasked; facts from an unasked person's private notes are not supplied to synthesis. The prototype doesn't claim Slack/Google Calendar integrations exist.

Personas receive only their own name/title/expertise/context plus the question. The model selects note indices; code renders one to three grouped sentences from those notes, preventing invented persona facts. Out-of-context answers explicitly say they do not know. Cache keys include person, question hash and context content, so changed facts do not reuse stale answers. Model-generated next-person decisions are cached in Delta checkpoints by the full planning input, so identical reruns reuse the same questions as well as persona answers. Fresh answers also bypasses this planning cache. Temperature zero alone is not assumed to make a serving endpoint mathematically deterministic.

`DWELL_SECONDS=1` is the default minimum dwell, including cached answers. Movement takes 450ms and old replays cap each asking pause at one second; SQL/model latency can make visits longer. Build & push publishes generated source to a new GitHub branch by default. Preview only saves files in Delta; its completion card can publish those same files without regenerating them. Configure the GitHub App secret described above before publishing. All activity comes from the event table; static layout, UI chrome, and graph/data tables are not activity events.

Approval for **6–10 consecutive days is unspecified**. Generated software must flag it rather than invent authority. Demo logic interprets notice length as business days and notice lead time as calendar days; the generated README states that assumption. Real Google Workspace login and live balances are out of scope for the generated small mock app. Do not use it with real employee data.

Dry run writes no generated files to the Git folder or GitHub and creates no branches/commits. Delta run history, cache, and generated artifact records are still saved for inspection/replay. This is the scope of “writes nothing”; a literal zero-write run could not support required persisted events and replay.

## Verification

```sh
.venv/bin/python -m pytest -q
node --check static/app.js
.venv/bin/python scripts/validate_bundle.py
```

Generated tests run only when a suitable Node runtime exists. The test process gets no environment credentials, has a bounded timeout, and a restricted filesystem; the standalone logic module also runs against independent contract tests. Results include real exit codes/output or explicitly say `not run`. Generation has at most two retries after the initial attempt. Local fixture tests do not count as live deployment acceptance.

Development uses `.worktrees/fernwood-agent-watcher` and shares the existing Python environment to avoid reinstalling dependencies. No worktree paths enter runtime configuration. Obsolete scenario sources/tests/docs are removed from this checkout; Git history and previously created external tables are not erased.

## Documentation checked

- [Databricks App secrets](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/secrets)
- [App runtime, including Node.js](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/system-env)
- [Git CLI compute support and Git-folder limitations](https://docs.databricks.com/aws/en/repos/git-operations-with-repos/)
- [Repos SDK API](https://databricks-sdk-py.readthedocs.io/en/stable/workspace/workspace/repos.html)
- [GitHub commit API and Contents permission](https://docs.github.com/en/rest/git/commits)
- [Tavily search and retrieved content](https://docs.tavily.com/documentation/api-reference/endpoint/search)
