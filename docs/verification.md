# Fernwood verification — November 15, 2026

App: https://agent-watcher-7474649299876234.aws.databricksapps.com

Live successful run: `0ab8ce02f6c54278b192d9c34438aba8`. Pipeline batch: `8a5282517a70489596113c3b614a82bd` in `workspace.fernwood`.

| # | Acceptance criterion | Result and evidence |
|---|---|---|
| 1 | Exact pipeline counts and Catalog visibility | **PARTIAL:** Databricks Job succeeded. Physical Delta counts are 12 → 10, 1 fuzzy merge, 1 exact collapse, 2 quarantine groups, 8 valid collaborations. Volume bytes match all three checked-in CSVs. Table/column comments and Delta registration verified. Catalog Explorer browser redirects to interactive SSO, so its visual check is **blocked**, not assumed. |
| 2 | Data view shows actual layers/quarantine | **PASS:** Fresh private browser visited Raw CSV, Bronze, Silver, Gold and the two quarantine rows, all through live table queries. |
| 3 | People graph/review edges/hover | **PASS:** Ten person nodes, reporting and collaboration edges, two dashed candidate-manager edges for Jordan, a missing-manager stub for Nina, two review nodes and working hover/pinned cards. No eleventh fictional manager node. |
| 4 | Live agent visits ≥6 people, dwell, grounded simulated answers | **PASS:** Nine distinct people, each answered after at least 3000ms; answers labeled simulated. Physical answers checked against each person's own Gold context. |
| 5 | Real search and stored sources | **BLOCKED:** No Tavily provider key exists. The run emitted searching/search_unavailable events and fabricated no sources. Configure secret `TAVILY_API_KEY`. Provider integration has fixture tests, not a claimed live search pass. |
| 6 | Preflight and real Git commits | **BLOCKED:** App-runtime preflight ran. Folder read and GitHub connectivity passed; Workspace API write failed for the Git CLI folder. An App-triggered serverless probe created a local commit but GitHub rejected push with 403. Cleanup restored main and removed the probe file. No remote commits are claimed. Fallback GitHub API writer is implemented and tested with API doubles; configure a fine-grained `GITHUB_TOKEN` for Kman771/Test-Repo, Contents read/write. Generated README attribution was verified independently of publishing. |
| 7 | Events-only activity; stable cached answers | **PASS:** Browser activity consumes physical events. Physical events equal the durable outbox. Cached answers were reused and matched Delta cache records. Model-generated planning decisions now also use a persisted input-keyed cache, tested against a varying model double. |
| 8 | Replay and dry run | **PASS:** Browser replay reproduced event count with REPLAY badge. Dry run displayed generated source and emitted no commit events; writer exclusion is also unit tested. Delta run history/cache/artifacts are deliberately persisted. |
| 9 | Deployed URL, private browser, live UC | **PASS:** Fresh private Chromium context with externally obtained OAuth scoped only to the App origin; no stored cookies. Graph/Data/run read live Unity Catalog. Mobile overflow check passed, zero JavaScript errors. |
| 10 | Secrets and obsolete scenario cleanup | **PASS within current checkout/runtime scope:** Old scenario code/data/docs and unused gateway/simulated-repo modules removed. Source and live run scans found no credential-format matches; Setup exposes booleans, never tokens. Authentication stays in SDK/CLI stores or App secret resources. Git history and historical external schemas are retained, not erased. |
| 11 | Honest test results | **PASS:** 25 Python tests passed. Successful live generation ran 10 Node tests on Databricks Node v22.23.4, exit 0, with actual TAP output in the event table. The actual live-run artifact (154 lines including its cited README) also passed TypeScript/Vite build and its npm tests locally; a separate served-model preflight passed browser request/approve/persist/cancel checks. Two unknown answers were retained as open questions. |

The first live verification run (`441a64b41166428c88c5f11b3adfe3cf`) correctly ended with an error after generation/test failures. It was not relabeled as successful. The Node runner was made compatible with the actual App runtime, the source generator gained a reviewed small reference scaffold plus required UI checks, and a new cached run succeeded. Generation is limited to the initial attempt plus two retries, with attempt counts persisted across recovery.

## Preflight path and outstanding setup

Destination: `/Workspace/Users/kaashish.v@gmail.com/.bundle/Test-Repo`, sibling of `agent-watcher`, with remote https://github.com/Kman771/Test-Repo. This Git CLI folder is not returned by the standard Repos list endpoint. GitHub currently has only main; test pushes did not land.

Selected fallback: **GitHub REST API, pending its repo-scoped secret**. Alternatively, fix the linked Databricks GitHub OAuth installation's write access to the repository and rerun the Git CLI probe; the current build writer uses the documented API fallback. A secret cannot be derived from general editing permission.

Search: Tavily, pending `TAVILY_API_KEY`. README provides exact secret-resource setup. Both missing integrations are visible in Setup and in run outcomes. No mock search, mock commits, or runtime local database substitutes are used.

Catalog visual check: open https://dbc-4702f9e0-0c88.cloud.databricks.com/explore/data/workspace/fernwood/nodes in a signed-in workspace browser. The SDK/SQL checks passed; interactive SSO remains separate.

## Artifacts

- `delta-verification.json`: real pipeline, volume, table-comment checks.
- `preflight.json`, `git-preflight-result.json`: App and serverless preflight evidence.
- `browser-verification.json`: private deployed browser, live run, replay and actual tests.
- `run-record-verification.json`: physical events, source grounding, cache and attribution.
- `generated-app-verification.json`: separate served-model code/build/browser preflight.
- `catalog-verification.json`: Catalog SSO result.
- `source-audit.json`: current worktree scan scope and results.

Screenshots and generated inspection copies live under ignored `data/`; credentials/cookies are never saved. Work was isolated in `.worktrees/fernwood-agent-watcher`, using the original Python environment for speed.
