"""Run the four-condition benchmark from the command line.

    python scripts/benchmark.py            # writes data/benchmark.json and prints a table

Set AFD_GENIE_SPACE_ID to include the "Genie only" condition.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("AFD_TODAY", "2026-11-14")  # pin "today" so edge ages are reproducible

from afd.benchmark import CASES, CONDITIONS, run_benchmark  # noqa: E402
from afd.config import get_settings  # noqa: E402


def main() -> None:
    out = run_benchmark(Path(get_settings().data_dir))
    width = 22
    print("case".ljust(18), *(c["label"].ljust(width) for c in CONDITIONS))
    for case in CASES:
        cells = []
        for c in CONDITIONS:
            r = out["results"][c["id"]][case["id"]]
            cells.append(("-" if r.get("skipped") else f"{'OK ' if r['correct'] else 'X  '}{r['outcome_label']}").ljust(width))
        print(case["id"].ljust(18), *cells)
    print()
    for c in CONDITIONS:
        s = out["summary"][c["id"]]
        if s.get("skipped"):
            print(f"{c['label']:<22} skipped ({s['skipped']})")
        else:
            print(f"{c['label']:<22} accuracy {s['accuracy']:.0%} ({s['correct']}/{s['total']}), "
                  f"claims with evidence {s['evidence_share']:.0%}, avg steps {s['avg_steps']:.1f}")


if __name__ == "__main__":
    main()
