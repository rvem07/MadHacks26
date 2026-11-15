# Wildlife-protection demo verification

The current default UI contains only the task prompt, employee graph, and summarized workflow. The guided scenario starts with Maria, then asks Priya, Rachel, Lena, and Tom. The generic backend and all previous tests remain available.

Backend checks cover: explicit scenario routing independent of requester; five decisions and final specification; vague Other answers staying with the same employee; concrete explanations; invalid/empty options; each stage's decision validation; refresh/restart persistence; unchanged graph; operation without a hosted model; and structured API answers with text-answer compatibility.

The browser script now checks the simplified demo rather than the old inbox/control UI. Executed results: **57 backend tests passed** (all 44 previous tests plus 13 wildlife-demo tests); the full browser flow passed with zero page errors, including mobile layout and refresh recovery. JavaScript syntax, Python compilation, whitespace checks, and offline Databricks bundle validation also passed. Screenshot: ignored `data/wildlife-demo.png`. No Databricks workspace execution was performed. Prior generic-backend results follow as historical context.

---

# Agent Watcher verification

Implemented and verified locally. No Databricks workspace was contacted; the deployment bundle was checked against the installed CLI's offline schema.

## Executed checks

| Acceptance criterion | Local result | Evidence |
|---|---|---|
| Messy sample → bronze/silver/gold, quarantine, stats | PASS | `scripts/run_pipeline.py`: 24 inputs, 2 merges, 14 employees, 9 reporting edges, 7 owned topics, 6 quarantine records in 4 groups. Raw Tom email retains casing/trailing space in bronze. |
| Routing tool creates a request and suspends | PASS | Network outage routes to Priya. Run persists in `waiting_on_human`; no waiting agent worker remains. |
| Live graph shows pause on correct employee | PASS | Headless Chrome asserted Priya's `waiting` class and visible question/inbox. |
| Answer resumes; all steps visible | PASS | Browser answered as Priya, followed her referral to Tom, answered as Tom, and verified completion and the employee-provided facts in the result. Backend checks event sequence and resumed/done states. |
| Manual manager reroute | PASS | Priya → Tom; new request links to superseded request. Old answers are rejected. Browser verified the manager highlight. |
| Cancel | PASS | Browser and backend verified terminal cancellation, no pending request, and rejection of late answers. |
| No escalation timers/timeouts | PASS for Watcher | Source audit checks the active workflow for timer/waiter/sleep/auto-reply constructs. Human waiting returns from execution. SSE/inbox refresh and SQL transport completion checks do not drive escalation. Legacy demo timers remain isolated in opt-in legacy code. |

Additional backend coverage: restart while waiting; recovery of an accepted answer not yet dispatched; replay of saved next action without a new model call; duplicate submissions; incorrect identity; concurrent answer/cancel; no validated manager; named referrals; absent CEO referral; unknown-topic clarification; pipeline reruns with pinned graph batches; projection failure recovery; parameterized insert-only bronze projections; physical Delta DDL generation; existing API compatibility; invalid request validation; model-driven decisions through an injected adapter.

Final backend command: `.venv/bin/python -m pytest -q` — **44 passed**. This includes all 23 pre-existing tests. The installed latest FastAPI/Starlette versions emit three deprecation warnings; no check failed.

Final browser command: `.venv/bin/python scripts/verify_watcher_browser.py` — **PASS**, zero page errors. Browser dependency is optional and separate from runtime requirements. Screenshot: ignored `data/watcher-browser.png`.

Other checks: `scripts/validate_bundle.py`, Python compilation, JavaScript syntax, and `git diff --check` — **PASS**. Offline bundle validation checks structure, not workspace permissions/resource availability or execution.

## Pending Databricks checks

These require later workspace access and are **not claimed as passed**:

- Execute bootstrap/DDL and the pipeline Job against actual Unity Catalog Delta tables.
- Verify app/pipeline service identities, warehouse access, table grants, and model resource permissions.
- Start the Databricks App, test hosted-model routing/questions/referrals, and repeat browser acceptance there.
- Restart the deployed app and verify recovery from Delta, including a deliberately interrupted projection.
- Enable an MLflow experiment and verify spans appear for actual hosted execution.

## Chosen defaults and limits

- Graph: employees and `reports_to` edges; team and topic information stays in attributes/routing tables.
- Sample demo: network outage, not payments outage. No extra payments responsibility was invented.
- Priya's two emails are explicitly merged through the checked-in alias configuration.
- Employees with invalid reporting information are retained; invalid relationships are quarantined.
- CEO is not a known title in the supplied directory; ambiguous referrals require clarification.
- Local mode uses a labeled deterministic adapter. Actual Databricks model responses have not been tested here.
- Watcher uses the existing Databricks model client directly for checkpointed decisions; the original coding/Agent Bricks host remains in the legacy workflow.
- One app process/replica is required. This is a hackathon-scale durable workflow, not a distributed task queue.
- The separate `agent_watcher` schema preserves old demo tables and runs. Future incompatible Watcher changes require an explicit migration or a new schema.
- Act-as is a demo selector; nudge does not send an external message. No external communication or deployment was performed.
