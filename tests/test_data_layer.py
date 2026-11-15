"""Stage 1: store, versioned ontology, versioned config, repo, audit."""
from datetime import date

import pytest

from afd.agentcfg import ConfigError, ConfigService, render_instructions
from afd.ontology import Graph, OntologyError, OntologyService
from afd.repo import ProtectedBranchError, Repo
from afd.store import LocalStore


def test_store_persists_across_instances(tmp_path):
    s = LocalStore(tmp_path)
    s.insert("runs", {"run_id": "r1", "status": "running"})
    s.update("runs", "run_id", "r1", {"status": "done"})
    again = LocalStore(tmp_path)
    assert again.get("runs", run_id="r1")["status"] == "done"


def test_seed_has_demo_stale_edge(store):
    g = OntologyService(store).graph()
    assert g.version == 1
    people = [n for n in g.nodes.values() if n["type"] == "person"]
    assert len(people) == 7
    stale = g.edge("e_priya_owns_secspec")
    assert g.describe_edge(stale) == "Priya Shah owns Security specs"
    assert Graph.edge_age_days(stale, date(2026, 11, 14)) > 365


def test_ontology_ops_version_and_audit(store):
    onto = OntologyService(store)
    v2 = onto.apply_ops([{"op": "update_edge", "id": "e_priya_owns_secspec",
                          "set": {"src": "p_marcus", "last_verified": "2026-11-14"}}], author="dana")
    assert v2["version"] == 2
    assert onto.graph(2).edge("e_priya_owns_secspec")["src"] == "p_marcus"
    # The old version is untouched, so past runs still replay against it.
    assert onto.graph(1).edge("e_priya_owns_secspec")["src"] == "p_priya"
    last = store.all("audit_log")[-1]
    assert last["actor"] == "dana" and last["target"] == "ontology@v2"
    assert last["before"][0]["src"] == "p_priya" and last["after"][0]["src"] == "p_marcus"


def test_ontology_rollback_creates_new_version(store):
    onto = OntologyService(store)
    onto.apply_ops([{"op": "delete_edge", "id": "e_priya_owns_secspec"}], author="sam")
    v3 = onto.rollback(1, author="sam")
    assert v3["version"] == 3
    assert onto.graph(3).edge("e_priya_owns_secspec") is not None
    assert [v["version"] for v in onto.versions()] == [1, 2, 3]


def test_ontology_rejects_bad_edits(store):
    onto = OntologyService(store)
    with pytest.raises(OntologyError):
        onto.apply_ops([{"op": "add_edge", "edge": {"src": "p_sam", "dst": "nope", "type": "owns"}}], "sam")
    with pytest.raises(OntologyError):
        onto.apply_ops([{"op": "add_edge", "edge": {"src": "p_sam", "dst": "svc_auth", "type": "likes"}}], "sam")
    assert onto.current_version() == 1  # nothing half-applied


def test_graph_search_finds_task(store):
    g = OntologyService(store).graph()
    hits = g.search("build the backend for the invoicing feature", types=["task"])
    assert hits[0]["id"] == "task_invoicing_backend"


def test_config_ops_and_rollback(store):
    cfg = ConfigService(store)
    cfg.apply_ops([{"op": "add_rule", "stage": "investigate",
                    "rule": {"id": "r_stale", "text": "confirm stale owners",
                             "check": {"type": "stale_owner", "max_age_days": 180}}}], author="leo")
    assert cfg.current_version() == 2
    rules = [r["id"] for r in cfg.get()["config"]["stages"][1]["rules"]]
    assert "r_stale" in rules
    cfg.rollback(1, author="leo")
    assert "r_stale" not in [r["id"] for r in cfg.get()["config"]["stages"][1]["rules"]]


def test_config_validation(store):
    cfg = ConfigService(store)
    bad = cfg.get()["config"]
    bad["stages"][0]["allowed_tools"].append("rm_rf")
    with pytest.raises(ConfigError):
        cfg.replace(bad, author="ana")


def test_render_instructions_mentions_rules(store):
    cfg = ConfigService(store).get()
    md = render_instructions(cfg["config"], cfg["version"])
    assert "## Stage: investigate" in md and "Every claim in the conclusion must cite evidence." in md


def test_repo_protects_main(store):
    repo = Repo(store)
    with pytest.raises(ProtectedBranchError):
        repo.write("x.py", "print(1)", branch="main")
    repo.create_branch("agent/t1")
    repo.write("services/invoicing/app.py", "x = 1\n", branch="agent/t1")
    pr = repo.open_pr("agent/t1", "t", "b", author="agent")
    assert pr["files"] == [{"path": "services/invoicing/app.py", "status": "added", "lines": 2}]
    assert "services/invoicing/app.py" not in repo.list_files("main")
