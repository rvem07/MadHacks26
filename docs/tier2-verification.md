# Tier 2 verification

App: https://agent-watcher-7474649299876234.aws.databricksapps.com

Development worktree: `.worktrees/sentinel-tier2`, branch `sentinel-tier2`, based on working Tier 1 commit `5a54a3a`. Dependencies are shared with the main checkout, not reinstalled or changed. Tier 1's collection flow, data, and checkpoint states remain in use.

| Check | Result |
|---|---|
| Unit/API suite | 85 passed, including the headless browser fixture |
| Headless browser fixture | Passed: plan grouping, cited source click/highlight, task owner glow and Markdown download, plus original sequential/concurrent/manual/refresh/mobile checks |
| Bundle schema | Passed |
| Real served-model synthesis preflight | Passed against existing UC persona answers; uncovered and corrected summary length handling and confusion between questions and factual assertions |
| Live Run all → structured plan | PASS — seven tasks, each assigned through ontology routing to a real, non-quarantined Gold person |
| Live cited findings and graph source clicks | PASS — every finding checked against its persona answer; every displayed source link clicked and corresponding node highlight observed |
| Live unresolved question | PASS — viewer identities appear in open_questions rather than being invented |
| Live Markdown download | PASS — downloaded plan.md exactly equals the stored plan's canonical Markdown |
| Physical plans row and UC table registration | PASS — independently read plan_json/created_at through SQL and table/columns through the UC SDK; format DELTA |
| Catalog Explorer visual check | BLOCKED by interactive workspace SSO — login screen observed, so no visual UI pass is claimed |

Assumptions: proposed tasks may share canonical topics; model-generated owner choices are ignored and resolved through the ontology. Summary sentences, findings, requirements, task description constraints and risk excerpts are extractive; proposed workflow titles and the full plan are additionally model-audited. Unknowns remain open questions; policy never constitutes approval. Historical completed Tier 1 runs are preserved without automatic plan generation. A new additive plans table is created using deployer privileges; the App continues using inherited SELECT/MODIFY grants. Catalog Explorer may require separate interactive workspace SSO even when the App API accepts OAuth.


Live run ID: `bd368d1ecf1842268553f3e02adfe5bf` (actual Run all, five served-model personas, no injected responses). The run's plan survived App redeployment and was reopened for final download/persistence checks. The final verifier completed around **09:01 UTC, 2026-11-15** with zero App JavaScript errors. Machine-readable results: [tier2-live-results.json](tier2-live-results.json).

Tier 1 physical regression check also passed after deployment: batch `74d876c5d9664c10b43a22669283c07e` remains active with 12 raw people, 10 people, 1 identity merge, 4 quarantine groups/6 retained records, 23 nodes, 23 edges, and 25 context rows. No ingestion batch was added.

To complete the remaining visual check, sign in and open [Catalog Explorer → plans](https://dbc-4702f9e0-0c88.cloud.databricks.com/explore/data/workspace/sentinel_gold/plans), inspect the `run_id`, `plan_json`, `created_at` columns, and use Sample Data or SQL to see the stored record. The user was asked for confirmation of this UI access; it is not assumed from successful API access.

The initially tested download navigation was cancelled by the authenticated browser environment. The final UI uses a Blob containing the canonical Markdown already fetched from the physical plans table; direct `/plan.md` API export remains available. Both the fixture and deployed download now pass. The first live verifier attempt also clicked before initial preferences loaded; the verifier now waits for all ten controls. These are completed fixes, not unresolved acceptance failures.

Reproduce final checks without calling personas again:

```bash
.venv/bin/python scripts/verify_tier2_browser.py --run-id bd368d1ecf1842268553f3e02adfe5bf
```

Omit `--run-id` to execute a fresh Run all and synthesize another plan. The verifier requires the existing external Databricks CLI OAuth login; tokens and cookies are never saved to the repository.
