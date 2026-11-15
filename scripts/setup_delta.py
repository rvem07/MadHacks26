"""Create the Unity Catalog schema/tables and sync the local store into Delta.

    python scripts/setup_delta.py --warehouse-id <id> [--catalog workspace] [--schema agent_flow] [--sync]

  * Runs sql/schema.sql (CREATE ... IF NOT EXISTS, so it is safe to re-run).
  * --sync replays every row of the local JSON store (seeding it first if
    empty) into the Delta tables, then prints row counts.

Auth is whatever the Databricks SDK resolves (CLI profile / env vars).
After this, run the app with AFD_DELTA=1 AFD_WAREHOUSE_ID=<id> so new writes
are mirrored continuously.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from databricks.sdk import WorkspaceClient  # noqa: E402

from afd.config import get_settings  # noqa: E402
from afd.seed import seed_all  # noqa: E402
from afd.store import TABLES, DeltaMirror, LocalStore, _delta_rows  # noqa: E402


def run_sql(w: WorkspaceClient, warehouse_id: str, sql: str):
    resp = w.statement_execution.execute_statement(statement=sql, warehouse_id=warehouse_id, wait_timeout="50s")
    state = resp.status.state.value
    if state != "SUCCEEDED":
        raise RuntimeError(f"{state}: {resp.status.error.message if resp.status.error else ''}\n{sql[:200]}")
    return resp


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--warehouse-id", required=True)
    ap.add_argument("--catalog", default="workspace")
    ap.add_argument("--schema", default="agent_flow")
    ap.add_argument("--sync", action="store_true", help="copy the local store into Delta")
    args = ap.parse_args()

    w = WorkspaceClient()
    fq = f"`{args.catalog}`.`{args.schema}`"
    ddl = (ROOT / "sql" / "schema.sql").read_text().replace("${fq}", fq)
    # Drop full-line comments first (they may contain ';'), then split statements.
    code = "\n".join(line for line in ddl.splitlines() if not line.strip().startswith("--"))
    for stmt in (s.strip() for s in code.split(";") if s.strip()):
        print("->", stmt.splitlines()[0][:90])
        run_sql(w, args.warehouse_id, stmt)

    if args.sync:
        store = LocalStore(get_settings().data_dir)
        seed_all(store)
        mirror = DeltaMirror.__new__(DeltaMirror)  # synchronous use: no background thread
        mirror.fq, mirror.warehouse_id, mirror.client = fq, args.warehouse_id, w
        for table in TABLES:
            batches: dict[str, list] = {}
            for row in store.all(table):
                for delta_table, cols in _delta_rows(table, row):
                    batches.setdefault(delta_table, []).append(cols)
            for delta_table, rows in batches.items():
                for i in range(0, len(rows), 40):
                    mirror.insert_rows(delta_table, rows[i:i + 40])
                print(f"synced {len(rows):>4} rows -> {delta_table}")

    for t in ["ontology_versions", "nodes", "edges", "agent_config_versions", "events", "runs"]:
        r = run_sql(w, args.warehouse_id, f"SELECT count(*) FROM {fq}.`{t}`")
        print(f"{t:<24} {r.result.data_array[0][0]} rows")


if __name__ == "__main__":
    main()
