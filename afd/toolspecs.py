"""The agent's tool catalogue: names, descriptions, parameters, classification.

One source of truth used by:
  * agentcfg.validate     (allowed_tools must name real tools)
  * tools.ToolWrapper     (action_type for events, app/level for the allowlist)
  * agentbricks           (tool descriptions/JSON schemas exposed to the
                           Agent Bricks Supervisor Agent)
  * the UI                (rule editor checkboxes, graph action markers)

Fields:
  action_type  one of the spec's event action types
  app          external app the tool touches (None = internal)
  level        "read" or "write": checked against the stage's app allowlist
  marker       small glyph key the graph shows on the node that triggered it
"""
from __future__ import annotations

ACTION_TYPES = ["observe", "traverse", "query", "decide", "ask_human", "act", "conclude", "blocked"]

TOOL_SPECS: dict[str, dict] = {
    "begin_stage": {
        "description": "Announce the stage you are entering (triage, investigate, act, verify).",
        "params": {"stage": "string"}, "action_type": "observe", "app": None, "level": "read", "marker": None,
    },
    "ontology_lookup": {
        "description": "Find ontology nodes by id or by words in their label. Returns nodes with type and label.",
        "params": {"query": "string", "types": "array<string>?"},
        "action_type": "query", "app": None, "level": "read", "marker": None,
    },
    "ontology_traverse": {
        "description": ("List the neighbors of a node along allowed edge types. Each result includes the edge's "
                        "provenance: source, confidence, last_verified and age_days."),
        "params": {"node_id": "string", "edge_types": "array<string>?", "direction": "in|out|both"},
        "action_type": "traverse", "app": None, "level": "read", "marker": None,
    },
    "record_decision": {
        "description": "Record a decision: the options you considered, the one you chose, and why.",
        "params": {"options": "array<string>", "chosen": "string", "rationale": "string",
                   "node_ids": "array<string>?", "edge_ids": "array<string>?"},
        "action_type": "decide", "app": None, "level": "read", "marker": None,
    },
    "cite_evidence": {
        "description": "Attach evidence (event ids like run:seq, or node/edge ids) to a claim.",
        "params": {"claim": "string", "evidence_refs": "array<string>"},
        "action_type": "decide", "app": None, "level": "read", "marker": None,
    },
    "ask_human": {
        "description": ("Ask a teammate a question and WAIT for the reply. Pass the edge that made you pick "
                        "this person in edge_ids. Times out per the stage's escalation settings."),
        "params": {"person_id": "string", "question": "string", "edge_ids": "array<string>?"},
        "action_type": "ask_human", "app": None, "level": "read", "marker": "ask",
    },
    "message_human": {
        "description": "Send a teammate a one-way message (in-app, mirrored to Gmail). Does not wait.",
        "params": {"person_id": "string", "text": "string"},
        "action_type": "act", "app": "gmail", "level": "write", "marker": "message",
    },
    "read_file": {
        "description": "Read a file from the repo (your branch if it exists, else main).",
        "params": {"path": "string"}, "action_type": "observe", "app": "github", "level": "read", "marker": "github",
    },
    "edit_file": {
        "description": "Create or overwrite a file on your agent/ branch.",
        "params": {"path": "string", "content": "string", "branch": "string?"},
        "action_type": "act", "app": "github", "level": "write", "marker": "github",
    },
    "analyze_csv": {
        "description": "Profile a CSV in the repo: columns, row count, numeric summaries, value counts.",
        "params": {"path": "string"}, "action_type": "query", "app": "github", "level": "read", "marker": "csv",
    },
    "web_search": {
        "description": "Search the public web (simulated offline; Databricks web search under Agent Bricks).",
        "params": {"query": "string"}, "action_type": "query", "app": "web", "level": "read", "marker": "web",
    },
    "genie_query": {
        "description": "Ask the Genie space a data question about the company tables (read-only).",
        "params": {"question": "string"}, "action_type": "query", "app": "genie", "level": "read", "marker": "genie",
    },
    "github_create_branch": {
        "description": "Create your working branch. Name must start with the configured prefix (agent/).",
        "params": {"name": "string"}, "action_type": "act", "app": "github", "level": "write", "marker": "github",
    },
    "github_open_pr": {
        "description": "Open a pull request from your branch into main.",
        "params": {"title": "string", "body": "string"},
        "action_type": "act", "app": "github", "level": "write", "marker": "github",
    },
    "conclude": {
        "description": "Finish the run with an answer and a list of claims, each with evidence_refs.",
        "params": {"answer": "string", "claims": "array<{text, evidence_refs}>"},
        "action_type": "conclude", "app": None, "level": "read", "marker": None,
    },
}


def json_schema(name: str) -> dict:
    """OpenAI-style function schema for a tool (used for LLM-hosted agents)."""
    spec = TOOL_SPECS[name]
    props: dict = {"run_id": {"type": "string", "description": "The run id you were given."}}
    required = ["run_id"]
    for pname, ptype in spec["params"].items():
        optional = ptype.endswith("?")
        ptype = ptype.rstrip("?")
        if ptype.startswith("array"):
            item = {"type": "object"} if "{" in ptype else {"type": "string"}
            props[pname] = {"type": "array", "items": item}
        elif "|" in ptype:
            props[pname] = {"type": "string", "enum": ptype.split("|")}
        else:
            props[pname] = {"type": "string"}
        if not optional:
            required.append(pname)
    return {"type": "function", "function": {"name": name, "description": spec["description"],
                                             "parameters": {"type": "object", "properties": props,
                                                            "required": required}}}
