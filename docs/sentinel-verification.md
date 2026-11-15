# Sentinel verification — 2026-11-15

App: https://agent-watcher-7474649299876234.aws.databricksapps.com

Workspace: `https://dbc-4702f9e0-0c88.cloud.databricks.com`  
Warehouse: `1bc852920e5efadc`  
Unity Catalog schema: `workspace.sentinel_gold`  
Served model: `databricks-qwen3-next-80b-a3b-instruct` (temperature 0)  
Published batch: `74d876c5d9664c10b43a22669283c07e`

## Executed checks

| Check | Result | Evidence |
|---|---|---|
| Existing regression and new workflow tests | PASS | 69 passed; browser test skipped by default and run separately. Unit/API tests use explicit model/document doubles. |
| Real headless browser with explicit fixtures | PASS | Ten people, four flagged nodes, Ben waiting, five simultaneous waiting nodes, summary, Elena manual waiting, refresh recovery, typed resume, mobile layout, no page errors. |
| Bundle schema validation | PASS | Installed CLI schema and online workspace validation. |
| Databricks ingestion Job | PASS | Job `1102685125718217`, successful run `873020347559773`; no local persistence. |
| Physical Unity Catalog output | PASS | Independent SQL verifier read active-batch tables: 12 raw people, 10 unique people, 1 identity merge, 4 issue groups, 6 quarantine rows with all four reasons, 23 nodes, 23 edges, 25 context rows; unsafe owners excluded; Ben → Nadia → Elena chain. |
| App deployment | PASS | App running with one replica; resources and UC grants configured. |
| Live sequential pause/persona/resume | PASS | Real Ben → Mei → Carlos → Elena requests, waiting and resumed events, exposure count in Ben’s answer, source-grounded final summary. |
| Live parallel, manual, and unknown-persona checks | PASS | Multiple simultaneously glowing nodes observed in the deployed UI; five answered context requests and a summary; Elena manual waiting survives refresh, then typed answer resumes; out-of-context Ben reply declines and names someone who might know. |

## Acceptance criteria (all executed against deployed resources)

| # | Criterion | Result |
|---|---|---|
| 1 | Expected pipeline counts and populated quarantine reasons | PASS — physical Unity Catalog SQL check |
| 2 | Ben → Mei → Carlos → Elena with persisted waiting, persona answer, and resume | PASS — served model and live event stream |
| 3 | Run all: multiple glowing nodes and final summary | PASS — real browser graph class observation |
| 4 | Elena manual mode waits for a typed answer, then resumes | PASS — also refreshed while waiting and verified the same request ID |
| 5 | Fresh private browser context at deployed URL, reading live UC tables | PASS — OAuth-authenticated private context; no stored cookies |
| 6 | Out-of-context persona declines and names someone | PASS — Ben asked about the cafeteria menu |

The final deployed browser run completed at approximately **08:05 UTC on 2026-11-15** with **zero JavaScript page errors**. Machine-readable results: [sentinel-live-results.json](sentinel-live-results.json). Its screenshot is available locally at `data/sentinel-browser.png` and is intentionally ignored by Git. Local checks: **69 unit/API tests passed**, plus **one separately invoked headless-browser fixture test passed**. The browser test is skipped by the default suite unless explicitly enabled.

## Assumptions

- Four quarantine issues represent six retained source records. One duplicate identity is distinct from Jamal's repeated contradictory reporting assertion.
- Fresh/private browser access requires an authorized Databricks identity. Anonymous access is unsupported by Databricks Apps.
- Tier 1 uses the bounded Sentinel incident plan. Manual toggles apply to the next run; model calls already issued can finish after reset but cannot update a cancelled run.
- Test doubles are confined to explicit tests. Runtime refuses local storage/model fallback.

Initial live browser checks caught overly broad unknown handling for partial answers and an unsupported free-form synthesis. Both were fixed before the final acceptance rerun. Personas and summaries now render validated source excerpts.
