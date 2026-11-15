"""The ontology: a small, versioned company graph with edge provenance.

Data model (spec section 5)
  node: {id, type, label, props}
        type in NODE_TYPES
  edge: {id, src, dst, type, source, confidence, last_verified, props}
        type in EDGE_TYPES; `source` / `confidence` / `last_verified` are the
        provenance every edge must carry.

Versioning
  Every change writes a *full snapshot* as a new integer version. Snapshots
  are tiny (tens of nodes), and full snapshots make "replay a past run against
  the graph exactly as it was" trivial: look up the run's ontology_version.
  Rollback never deletes history; it writes a new version equal to an old one.
"""
from __future__ import annotations

import copy
from datetime import date
from typing import Any, Iterable

from . import audit
from .store import LocalStore

NODE_TYPES = ["person", "team", "service", "repo_area", "ticket", "task", "document"]
EDGE_TYPES = ["owns", "member_of", "depends_on", "affects", "works_on", "responsible_for"]

# Edge types that express "this person/team is accountable for that thing".
OWNERSHIP_EDGES = {"owns", "responsible_for"}


class OntologyError(ValueError):
    """Raised for invalid edits (unknown type, dangling edge, duplicate id...)."""


class Graph:
    """Read-only helper over one ontology snapshot."""

    def __init__(self, snapshot: dict):
        self.version: int = snapshot["version"]
        self.nodes: dict[str, dict] = {n["id"]: n for n in snapshot["nodes"]}
        self.edges: dict[str, dict] = {e["id"]: e for e in snapshot["edges"]}

    # -- lookups -----------------------------------------------------------
    def node(self, node_id: str) -> dict | None:
        return self.nodes.get(node_id)

    def label(self, node_id: str) -> str:
        n = self.nodes.get(node_id)
        return n["label"] if n else node_id

    def edge(self, edge_id: str) -> dict | None:
        return self.edges.get(edge_id)

    def search(self, text: str, types: Iterable[str] | None = None) -> list[dict]:
        """Rank nodes by how many words of `text` appear in their label/id/aliases."""
        words = {w for w in _tokens(text) if len(w) > 2}
        allowed = set(types) if types else None
        scored = []
        for n in self.nodes.values():
            if allowed and n["type"] not in allowed:
                continue
            hay = _tokens(" ".join([n["label"], n["id"], " ".join(n.get("props", {}).get("aliases", []))]))
            score = len(words & hay)
            if score:
                scored.append((score, n))
        scored.sort(key=lambda x: (-x[0], x[1]["id"]))
        return [n for _, n in scored]

    def neighbors(self, node_id: str, edge_types: Iterable[str] | None = None,
                  direction: str = "both") -> list[tuple[dict, dict]]:
        """(edge, other_node) pairs touching `node_id`.

        direction: "out" (node is src), "in" (node is dst) or "both".
        """
        types = set(edge_types) if edge_types else None
        out = []
        for e in self.edges.values():
            if types and e["type"] not in types:
                continue
            if direction in ("out", "both") and e["src"] == node_id and e["dst"] in self.nodes:
                out.append((e, self.nodes[e["dst"]]))
            if direction in ("in", "both") and e["dst"] == node_id and e["src"] in self.nodes:
                out.append((e, self.nodes[e["src"]]))
        return out

    @staticmethod
    def edge_age_days(edge: dict, today: date) -> int:
        """Days since the edge was last verified (provenance freshness)."""
        return (today - date.fromisoformat(edge["last_verified"])).days

    def describe_edge(self, edge: dict) -> str:
        """Human-readable edge, e.g. 'Priya Shah owns Security specs'."""
        return f"{self.label(edge['src'])} {edge['type'].replace('_', ' ')} {self.label(edge['dst'])}"


def _tokens(text: str) -> set[str]:
    import re

    return set(re.findall(r"[a-z0-9]+", text.lower().replace("_", " ")))


class OntologyService:
    """Versioned CRUD over the ontology, with audit and rollback."""

    def __init__(self, store: LocalStore):
        self.store = store

    # -- versions ----------------------------------------------------------
    def versions(self) -> list[dict]:
        """Version metadata (without the snapshot bodies), oldest first."""
        return [{k: v[k] for k in ("version", "created_at", "author", "note")} for v in
                self.store.all("ontology_versions")]

    def snapshot(self, version: int | None = None) -> dict:
        """Full snapshot for `version` (latest if None)."""
        rows = self.store.all("ontology_versions")
        if not rows:
            raise OntologyError("ontology has no versions; run the seed first")
        if version is None:
            return copy.deepcopy(rows[-1])
        for r in rows:
            if r["version"] == version:
                return copy.deepcopy(r)
        raise OntologyError(f"unknown ontology version {version}")

    def graph(self, version: int | None = None) -> Graph:
        return Graph(self.snapshot(version))

    def current_version(self) -> int:
        rows = self.store.all("ontology_versions")
        return rows[-1]["version"] if rows else 0

    def commit(self, nodes: list[dict], edges: list[dict], author: str, note: str,
               action: str = "edit", before: Any = None, after: Any = None) -> dict:
        """Validate and write a new version; record an audit row."""
        _validate(nodes, edges)
        version = self.current_version() + 1
        row = {"version": version, "created_at": audit.now_iso(), "author": author,
               "note": note, "nodes": nodes, "edges": edges}
        self.store.insert("ontology_versions", row)
        audit.record(self.store, author, f"ontology.{action}", f"ontology@v{version}", before, after, note)
        return row

    # -- edits -------------------------------------------------------------
    def apply_ops(self, ops: list[dict], author: str, note: str = "") -> dict:
        """Apply a list of edit operations atomically as one new version.

        Supported ops (also the format AI proposals use):
          {"op": "add_node",    "node": {...}}
          {"op": "update_node", "id": "...", "set": {...}}
          {"op": "delete_node", "id": "..."}            (also removes its edges)
          {"op": "add_edge",    "edge": {...}}
          {"op": "update_edge", "id": "...", "set": {...}}
          {"op": "delete_edge", "id": "..."}
        """
        snap = self.snapshot()
        nodes = {n["id"]: n for n in snap["nodes"]}
        edges = {e["id"]: e for e in snap["edges"]}
        before, after = [], []
        for op in ops:
            kind = op.get("op")
            if kind == "add_node":
                n = _normalize_node(op["node"])
                if n["id"] in nodes:
                    raise OntologyError(f"node {n['id']} already exists")
                nodes[n["id"]] = n
                before.append(None)
                after.append(n)
            elif kind == "update_node":
                n = _require(nodes, op["id"], "node")
                before.append(copy.deepcopy(n))
                n.update({k: v for k, v in op["set"].items() if k != "id"})
                after.append(copy.deepcopy(n))
            elif kind == "delete_node":
                n = _require(nodes, op["id"], "node")
                before.append(n)
                after.append(None)
                del nodes[op["id"]]
                for eid in [eid for eid, e in edges.items() if op["id"] in (e["src"], e["dst"])]:
                    del edges[eid]
            elif kind == "add_edge":
                e = _normalize_edge(op["edge"], existing=edges)
                edges[e["id"]] = e
                before.append(None)
                after.append(e)
            elif kind == "update_edge":
                e = _require(edges, op["id"], "edge")
                before.append(copy.deepcopy(e))
                e.update({k: v for k, v in op["set"].items() if k != "id"})
                after.append(copy.deepcopy(e))
            elif kind == "delete_edge":
                e = _require(edges, op["id"], "edge")
                before.append(e)
                after.append(None)
                del edges[op["id"]]
            else:
                raise OntologyError(f"unknown op {kind!r}")
        return self.commit(list(nodes.values()), list(edges.values()), author, note or "edit",
                           action="edit", before=before, after=after)

    def rollback(self, to_version: int, author: str) -> dict:
        """Write a new version identical to `to_version` (history is preserved)."""
        old = self.snapshot(to_version)
        cur = self.current_version()
        return self.commit(old["nodes"], old["edges"], author, f"rollback to v{to_version}",
                           action="rollback", before={"version": cur}, after={"version": to_version})


# -- validation helpers ------------------------------------------------------
def _require(table: dict, key: str, what: str) -> dict:
    if key not in table:
        raise OntologyError(f"unknown {what} {key}")
    return table[key]


def _normalize_node(n: dict) -> dict:
    for f in ("id", "type", "label"):
        if not n.get(f):
            raise OntologyError(f"node missing {f}")
    return {"id": n["id"], "type": n["type"], "label": n["label"], "props": n.get("props", {})}


def _normalize_edge(e: dict, existing: dict) -> dict:
    for f in ("src", "dst", "type"):
        if not e.get(f):
            raise OntologyError(f"edge missing {f}")
    eid = e.get("id") or f"e_{e['src']}_{e['type']}_{e['dst']}"
    if eid in existing:
        raise OntologyError(f"edge {eid} already exists")
    return {
        "id": eid, "src": e["src"], "dst": e["dst"], "type": e["type"],
        "source": e.get("source", "manual edit"),
        "confidence": float(e.get("confidence", 0.8)),
        "last_verified": e.get("last_verified") or date.today().isoformat(),
        "props": e.get("props", {}),
    }


def _validate(nodes: list[dict], edges: list[dict]) -> None:
    ids = set()
    for n in nodes:
        if n["type"] not in NODE_TYPES:
            raise OntologyError(f"node {n['id']}: type {n['type']!r} not in {NODE_TYPES}")
        if n["id"] in ids:
            raise OntologyError(f"duplicate node id {n['id']}")
        ids.add(n["id"])
    for e in edges:
        if e["type"] not in EDGE_TYPES:
            raise OntologyError(f"edge {e['id']}: type {e['type']!r} not in {EDGE_TYPES}")
        if e["src"] not in ids or e["dst"] not in ids:
            raise OntologyError(f"edge {e['id']} points at a missing node")
        if not (0.0 <= float(e["confidence"]) <= 1.0):
            raise OntologyError(f"edge {e['id']}: confidence must be within 0..1")
        date.fromisoformat(e["last_verified"])  # raises on malformed dates
        for f in ("source",):
            if not e.get(f):
                raise OntologyError(f"edge {e['id']} missing provenance field {f}")
