"""Storage: a local JSON store for working state, mirrored to Delta.

Why two layers?
  * The live UI needs low-latency reads/writes while a run is in flight
    (events arrive several times a second, a run pauses for a human, etc.).
  * The spec requires Delta tables in Unity Catalog as the system of record
    (nodes, edges, events, config versions, ...) so they can be queried,
    governed and read by Genie.

So the app keeps its working state in a small JSON-per-table store on local
disk and *mirrors* every write to Delta in the background (see DeltaMirror).
All mirrored tables are append-only; mutable records (runs, questions,
proposals) are re-appended with an `updated_at`, and readers take the latest
row per key. That keeps the Delta side simple and auditable.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
from pathlib import Path
from typing import Any, Callable, Iterable

log = logging.getLogger(__name__)

# Tables the app uses. Keeping the list explicit documents the data model.
TABLES = [
    "ontology_versions",      # full snapshot of nodes+edges per version
    "agent_config_versions",  # full agent config per version
    "audit_log",              # who changed what, when (before/after)
    "runs",                   # one row per run (mutable: status, answer, marks)
    "events",                 # the semantic event stream (append-only)
    "questions",              # ask_human questions and replies (mutable)
    "flags",                  # automatic post-mortem flags (append-only)
    "proposals",              # AI-proposed fixes (mutable: status)
    "test_cases",             # runs a teammate marked wrong, saved for regression
    "repo_state",             # simulated GitHub repo (branches, files, PRs)
]


class LocalStore:
    """Thread-safe, JSON-file-per-table store.

    Rows are plain dicts. The whole table is rewritten on each write, which
    is fine at hackathon scale (hundreds to low thousands of rows) and keeps
    the on-disk format human-readable for debugging.
    """

    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._tables: dict[str, list[dict]] = {}
        # Callbacks fired after every write: (table, row, op). Used by the Delta mirror.
        self._listeners: list[Callable[[str, dict, str], None]] = []
        for name in TABLES:
            path = self._path(name)
            self._tables[name] = json.loads(path.read_text()) if path.exists() else []

    # -- internals ---------------------------------------------------------
    def _path(self, table: str) -> Path:
        return self.dir / f"{table}.json"

    def _flush(self, table: str) -> None:
        tmp = self._path(table).with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._tables[table], indent=1, default=str))
        tmp.replace(self._path(table))  # atomic rename: no half-written files

    def _notify(self, table: str, row: dict, op: str) -> None:
        for listener in self._listeners:
            try:
                listener(table, row, op)
            except Exception:  # a mirror failure must never break the app
                log.exception("store listener failed for %s", table)

    # -- public API --------------------------------------------------------
    def subscribe(self, listener: Callable[[str, dict, str], None]) -> None:
        """Register a write listener (table, row, op) where op is insert|update."""
        self._listeners.append(listener)

    def insert(self, table: str, row: dict) -> dict:
        """Append a row and persist the table."""
        with self._lock:
            self._tables[table].append(row)
            self._flush(table)
        self._notify(table, row, "insert")
        return row

    def update(self, table: str, key: str, value: Any, fields: dict) -> dict | None:
        """Merge `fields` into the first row where row[key] == value."""
        with self._lock:
            for row in self._tables[table]:
                if row.get(key) == value:
                    row.update(fields)
                    self._flush(table)
                    snapshot = dict(row)
                    break
            else:
                return None
        self._notify(table, snapshot, "update")
        return snapshot

    def all(self, table: str) -> list[dict]:
        """A shallow copy of every row (safe to iterate while others write)."""
        with self._lock:
            return list(self._tables[table])

    def find(self, table: str, **where) -> list[dict]:
        """Rows matching every key=value in `where`."""
        return [r for r in self.all(table) if all(r.get(k) == v for k, v in where.items())]

    def get(self, table: str, **where) -> dict | None:
        """The first matching row, or None."""
        rows = self.find(table, **where)
        return rows[0] if rows else None

    def replace_all(self, table: str, rows: Iterable[dict]) -> None:
        """Overwrite a table (used only for the simulated repo snapshot)."""
        with self._lock:
            self._tables[table] = list(rows)
            self._flush(table)


# ---------------------------------------------------------------------------
# Delta mirror
# ---------------------------------------------------------------------------

# How each local table maps onto Delta columns. Each entry is
# (delta_table, [(column, sql_type, extractor)]). Complex values are stored
# as JSON strings so the Delta schema stays stable as the app evolves.
def _j(value: Any) -> str:
    return json.dumps(value, default=str)


def _delta_rows(table: str, row: dict) -> list[tuple[str, list[tuple[str, str, Any]]]]:
    """Translate one local row into one or more (delta_table, columns) rows."""
    if table == "ontology_versions":
        # One version row + the full node/edge snapshot for that version, so
        # `nodes` and `edges` can be queried "as of" any ontology_version.
        out = [("ontology_versions", [
            ("version", "INT", row["version"]), ("created_at", "TIMESTAMP", row["created_at"]),
            ("author", "STRING", row["author"]), ("note", "STRING", row.get("note", "")),
        ])]
        for n in row["nodes"]:
            out.append(("nodes", [
                ("ontology_version", "INT", row["version"]), ("node_id", "STRING", n["id"]),
                ("node_type", "STRING", n["type"]), ("label", "STRING", n["label"]),
                ("props", "STRING", _j(n.get("props", {}))),
            ]))
        for e in row["edges"]:
            out.append(("edges", [
                ("ontology_version", "INT", row["version"]), ("edge_id", "STRING", e["id"]),
                ("src", "STRING", e["src"]), ("dst", "STRING", e["dst"]),
                ("edge_type", "STRING", e["type"]), ("source", "STRING", e["source"]),
                ("confidence", "DOUBLE", e["confidence"]), ("last_verified", "DATE", e["last_verified"]),
                ("props", "STRING", _j(e.get("props", {}))),
            ]))
        return out
    if table == "agent_config_versions":
        return [("agent_config_versions", [
            ("version", "INT", row["version"]), ("created_at", "TIMESTAMP", row["created_at"]),
            ("author", "STRING", row["author"]), ("note", "STRING", row.get("note", "")),
            ("config", "STRING", _j(row["config"])),
        ])]
    if table == "events":
        return [("events", [
            ("run_id", "STRING", row["run_id"]), ("seq", "INT", row["seq"]),
            ("event_id", "STRING", row["event_id"]), ("ts", "TIMESTAMP", row["timestamp"]),
            ("stage", "STRING", row["stage"]), ("actor", "STRING", row["actor"]),
            ("action_type", "STRING", row["action_type"]), ("tool", "STRING", row.get("tool") or ""),
            ("node_ids", "STRING", _j(row["node_ids"])), ("edge_ids", "STRING", _j(row["edge_ids"])),
            ("input", "STRING", _j(row["input"])), ("result", "STRING", _j(row["result"])),
            ("status", "STRING", row["status"]), ("latency_ms", "INT", row["latency_ms"]),
            ("decision", "STRING", _j(row.get("decision"))), ("evidence_refs", "STRING", _j(row["evidence_refs"])),
            ("milestone", "BOOLEAN", row["milestone"]), ("plain_summary", "STRING", row.get("plain_summary") or ""),
            ("narrator", "STRING", row.get("narrator") or ""),
            ("config_version", "INT", row["config_version"]), ("ontology_version", "INT", row["ontology_version"]),
            ("external_ids", "STRING", _j(row.get("external_ids", {}))), ("warnings", "STRING", _j(row.get("warnings", []))),
            ("fidelity", "STRING", _j(row.get("fidelity", {}))),
        ])]
    # Generic mutable/append tables: key columns + the whole row as JSON.
    generic = {
        "runs": "run_id", "questions": "question_id", "flags": "flag_id",
        "proposals": "proposal_id", "test_cases": "test_case_id", "audit_log": "audit_id",
    }
    if table in generic:
        return [(table, [
            ("id", "STRING", row[generic[table]]),
            ("run_id", "STRING", row.get("run_id", "")),
            ("updated_at", "TIMESTAMP", row.get("updated_at") or row.get("created_at") or row.get("timestamp")),
            ("record", "STRING", _j(row)),
        ])]
    return []  # repo_state is local-only (the real system of record is GitHub)


class DeltaMirror:
    """Write-behind mirror: local store writes -> INSERTs into Unity Catalog.

    Writes are queued and flushed by one background thread in small batches
    through the SQL Statement Execution API, using named parameters (no string
    escaping). Failures are logged and counted, never raised into the app:
    the UI must keep working even if the warehouse is cold or unreachable.
    """

    def __init__(self, catalog: str, schema: str, warehouse_id: str, client=None):
        from databricks.sdk import WorkspaceClient  # imported lazily: optional dependency path

        self.fq = f"`{catalog}`.`{schema}`"
        self.warehouse_id = warehouse_id
        self.client = client or WorkspaceClient()
        self.q: queue.Queue = queue.Queue()
        self.errors = 0
        self.written = 0
        self._thread = threading.Thread(target=self._loop, name="delta-mirror", daemon=True)
        self._thread.start()

    def __call__(self, table: str, row: dict, op: str) -> None:
        """Store listener entry point: enqueue the translated rows."""
        for delta_table, cols in _delta_rows(table, row):
            self.q.put((delta_table, cols))

    def _loop(self) -> None:
        while True:
            first = self.q.get()
            batch = [first]
            # Drain whatever else is waiting (up to 50 rows) into one statement per table.
            while len(batch) < 50:
                try:
                    batch.append(self.q.get_nowait())
                except queue.Empty:
                    break
            by_table: dict[str, list] = {}
            for delta_table, cols in batch:
                by_table.setdefault(delta_table, []).append(cols)
            for delta_table, rows in by_table.items():
                try:
                    self.insert_rows(delta_table, rows)
                    self.written += len(rows)
                except Exception:
                    self.errors += 1
                    log.exception("delta mirror insert into %s failed", delta_table)

    def insert_rows(self, delta_table: str, rows: list[list[tuple[str, str, Any]]]) -> None:
        """INSERT a batch of rows using typed named parameters."""
        from databricks.sdk.service.sql import StatementParameterListItem

        columns = [c for c, _, _ in rows[0]]
        params: list = []
        tuples = []
        for i, row in enumerate(rows):
            placeholders = []
            for j, (col, sql_type, value) in enumerate(row):
                name = f"p{i}_{j}"
                placeholders.append(f"CAST(:{name} AS {sql_type})")
                params.append(StatementParameterListItem(
                    name=name, value=None if value is None else str(value).lower() if isinstance(value, bool) else str(value)
                ))
            tuples.append("(" + ", ".join(placeholders) + ")")
        sql = f"INSERT INTO {self.fq}.`{delta_table}` ({', '.join(columns)}) VALUES " + ", ".join(tuples)
        resp = self.client.statement_execution.execute_statement(
            statement=sql, warehouse_id=self.warehouse_id, parameters=params, wait_timeout="30s"
        )
        state = resp.status.state.value if resp.status and resp.status.state else "UNKNOWN"
        if state not in {"SUCCEEDED", "PENDING", "RUNNING"}:
            raise RuntimeError(f"insert failed: {state} {resp.status.error}")


_store: LocalStore | None = None


def get_store() -> LocalStore:
    """Process-wide store; attaches the Delta mirror when AFD_DELTA is on."""
    global _store
    if _store is None:
        from .config import get_settings

        s = get_settings()
        _store = LocalStore(s.data_dir)
        if s.delta_enabled and s.warehouse_id:
            try:
                _store.subscribe(DeltaMirror(s.catalog, s.schema, s.warehouse_id))
            except Exception:
                log.exception("could not start Delta mirror; continuing local-only")
    return _store


def reset_store(data_dir: Path) -> LocalStore:
    """Fresh store at `data_dir` (tests and the benchmark use isolated dirs)."""
    global _store
    _store = LocalStore(data_dir)
    return _store
