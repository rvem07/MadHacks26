"""Create a Genie space over the ontology views (read-only data tool).

    python scripts/setup_genie.py --warehouse-id <id> [--catalog workspace] [--schema agent_flow]

Prints the new space id; set AFD_GENIE_SPACE_ID to it so the agent's
genie_query tool and the benchmark's "Genie only" condition can use it.

The instructions describe the tables and the provenance columns, but do NOT
encode our agent's rules (e.g. "prefer fresh edges"): the Genie-only
benchmark condition is meant to be a plain "ask the data" baseline.
"""
import argparse
import json
import uuid

from databricks.sdk import WorkspaceClient


def _id() -> str:
    return uuid.uuid4().hex


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--warehouse-id", required=True)
    ap.add_argument("--catalog", default="workspace")
    ap.add_argument("--schema", default="agent_flow")
    args = ap.parse_args()
    fq = f"{args.catalog}.{args.schema}"
    space = {
        "version": 2,
        "config": {"sample_questions": sorted([
            {"id": _id(), "question": ["Who owns the security specs?"]},
            {"id": _id(), "question": ["Which edges have not been verified in the last 180 days?"]},
            {"id": _id(), "question": ["What does the invoicing service depend on?"]},
        ], key=lambda q: q["id"])},
        "data_sources": {"tables": sorted([{"identifier": f"{fq}.edges_current"}, {"identifier": f"{fq}.nodes_current"}],
                                          key=lambda t: t["identifier"])},
        "instructions": {"text_instructions": [{"id": _id(), "content": [
            "* This is the company ontology of Lumen Ledger, a seven-person startup.\n",
            "* edges_current has one row per relationship: src_label edge_type dst_label (e.g. 'Priya Shah owns Security specs').\n",
            "* 'owns' and 'responsible_for' edges say who is accountable for something; the person is src_label.\n",
            "* source, confidence and last_verified are provenance; age_days is days since last_verified.\n",
            "* When asked who owns something, return the person's full name (src_label) with the edge's source, confidence and last_verified.\n",
        ]}]},
    }
    w = WorkspaceClient()
    resp = w.api_client.do("POST", "/api/2.0/genie/spaces", body={
        "warehouse_id": args.warehouse_id, "serialized_space": json.dumps(space),
        "title": "Agent Flow Debugger ontology",
        "description": "Who owns what at Lumen Ledger: ontology nodes and edges with provenance.",
    })
    print("space_id:", resp.get("space_id"))
    print(f"export AFD_GENIE_SPACE_ID={resp.get('space_id')}")


if __name__ == "__main__":
    main()
