"""Stage 5: the benchmark harness produces the expected, honest pattern."""
from afd.benchmark import run_benchmark
from afd.config import get_settings


def test_benchmark_pattern(store, tmp_path):
    out = run_benchmark(tmp_path / "bench", get_settings())
    s, r = out["summary"], out["results"]
    assert s["genie_only"]["skipped"]                      # no Genie space in tests
    assert s["ontology_after_edit"]["accuracy"] > s["ontology"]["accuracy"]
    assert r["ontology"]["stale_owner"]["outcome"] == "asked:p_priya"
    assert r["ontology_after_edit"]["stale_owner"]["outcome"] == "asked:p_marcus"
    assert r["ontology_after_edit"]["loop"]["correct"]
    # The honest limit: a wrong mapping is a business fact the proposer can't see.
    assert not r["ontology_after_edit"]["wrong_mapping"]["correct"]
    # Controls are right everywhere.
    for cond in ("no_ontology", "ontology", "ontology_after_edit"):
        assert r[cond]["control_invoice"]["correct"] and r[cond]["control_auth"]["correct"]
