"""Agent configuration as data: per-stage goals, allowlists, rules, limits.

Spec section 6: agents are defined as data so non-technical teammates can
edit them. The config is versioned exactly like the ontology (full snapshot
per version + audit + rollback) and every run records the version it used.

Shape:
{
  "agent_name": "...", "description": "...",
  "repo": {"name": "lumen/ledger", "protected_branches": ["main"], "branch_prefix": "agent/"},
  "stages": [
    {
      "name": "investigate",
      "goal": "plain language",
      "allowed_node_types": [...], "allowed_edge_types": [...],
      "allowed_tools": [...],
      "rules": [{"id", "text", "enabled", "check": {"type": ..., ...} | null}],
      "escalation": {"ask_timeout_s": 120, "on_timeout": "escalate_to_second_source" | "stop",
                     "max_steps": 30, "stop_when": "plain language"},
      "apps": {"github": "read_write", "gmail": "read", ...}
    }, ...
  ]
}

Rules have two halves:
  * `text` is the plain-language rule a teammate reads and edits. It is what
    the LLM-hosted agent (Agent Bricks) is instructed with.
  * `check` (optional) is a structured version the tool wrapper and the
    scripted policy can enforce/act on deterministically. When a teammate
    accepts an AI proposal, both halves are written.
"""
from __future__ import annotations

import copy
from typing import Any

from . import audit
from .store import LocalStore

STAGES = ["triage", "investigate", "act", "verify"]

# Structured rule checks the system understands. Anything else is text-only.
CHECK_TYPES = {
    "stale_owner": "Owner edges older than max_age_days must be confirmed by a second source.",
    "fallback_repo_owner": "If nothing 'owns' the target, use who is responsible_for its repo area.",
    "no_revisit": "Never traverse into the same node twice; escalate instead of looping.",
    "require_evidence": "Every claim in the conclusion must cite at least one event or node.",
    "min_confidence": "Do not rely on edges below min_confidence without confirmation.",
}

APP_LEVELS = ["none", "read", "read_write"]


class ConfigError(ValueError):
    pass


class ConfigService:
    """Versioned agent config with audit, ops and rollback."""

    def __init__(self, store: LocalStore):
        self.store = store

    def versions(self) -> list[dict]:
        return [{k: v[k] for k in ("version", "created_at", "author", "note")} for v in
                self.store.all("agent_config_versions")]

    def get(self, version: int | None = None) -> dict:
        """Full version row {version, created_at, author, note, config}."""
        rows = self.store.all("agent_config_versions")
        if not rows:
            raise ConfigError("no agent config; run the seed first")
        if version is None:
            return copy.deepcopy(rows[-1])
        for r in rows:
            if r["version"] == version:
                return copy.deepcopy(r)
        raise ConfigError(f"unknown config version {version}")

    def current_version(self) -> int:
        rows = self.store.all("agent_config_versions")
        return rows[-1]["version"] if rows else 0

    def commit(self, config: dict, author: str, note: str, action: str = "edit",
               before: Any = None, after: Any = None) -> dict:
        validate(config)
        version = self.current_version() + 1
        row = {"version": version, "created_at": audit.now_iso(), "author": author,
               "note": note, "config": config}
        self.store.insert("agent_config_versions", row)
        audit.record(self.store, author, f"config.{action}", f"config@v{version}", before, after, note)
        return row

    def replace(self, config: dict, author: str, note: str = "direct edit") -> dict:
        """Direct edit from the rule editor: the whole config, applied immediately."""
        prev = self.get()["config"]
        return self.commit(config, author, note, before=_diff_summary(prev, config))

    def apply_ops(self, ops: list[dict], author: str, note: str = "") -> dict:
        """Apply rule/stage edits as one new version.

        Ops (also the AI-proposal format):
          {"op": "add_rule",    "stage": "investigate", "rule": {...}}
          {"op": "update_rule", "stage": "...", "rule_id": "...", "set": {...}}
          {"op": "delete_rule", "stage": "...", "rule_id": "..."}
          {"op": "set_stage_field", "stage": "...", "field": "goal|allowed_tools|...", "value": ...}
        """
        cfg = self.get()["config"]
        before, after = [], []
        for op in ops:
            stage = stage_by_name(cfg, op["stage"])
            kind = op.get("op")
            if kind == "add_rule":
                rule = {"id": op["rule"].get("id") or f"r_{len(stage['rules']) + 1}_{stage['name']}",
                        "text": op["rule"]["text"], "enabled": op["rule"].get("enabled", True),
                        "check": op["rule"].get("check")}
                if any(r["id"] == rule["id"] for r in stage["rules"]):
                    raise ConfigError(f"rule {rule['id']} already exists in {stage['name']}")
                stage["rules"].append(rule)
                before.append(None)
                after.append({"stage": stage["name"], "rule": rule})
            elif kind == "update_rule":
                rule = _rule(stage, op["rule_id"])
                before.append({"stage": stage["name"], "rule": copy.deepcopy(rule)})
                rule.update({k: v for k, v in op["set"].items() if k != "id"})
                after.append({"stage": stage["name"], "rule": copy.deepcopy(rule)})
            elif kind == "delete_rule":
                rule = _rule(stage, op["rule_id"])
                stage["rules"].remove(rule)
                before.append({"stage": stage["name"], "rule": rule})
                after.append(None)
            elif kind == "set_stage_field":
                before.append({"stage": stage["name"], op["field"]: copy.deepcopy(stage.get(op["field"]))})
                stage[op["field"]] = op["value"]
                after.append({"stage": stage["name"], op["field"]: op["value"]})
            else:
                raise ConfigError(f"unknown op {kind!r}")
        return self.commit(cfg, author, note or "edit", before=before, after=after)

    def rollback(self, to_version: int, author: str) -> dict:
        old = self.get(to_version)["config"]
        return self.commit(old, author, f"rollback to v{to_version}", action="rollback",
                           before={"version": self.current_version()}, after={"version": to_version})


# -- helpers -----------------------------------------------------------------
def stage_by_name(cfg: dict, name: str) -> dict:
    for s in cfg["stages"]:
        if s["name"] == name:
            return s
    raise ConfigError(f"unknown stage {name}")


def _rule(stage: dict, rule_id: str) -> dict:
    for r in stage["rules"]:
        if r["id"] == rule_id:
            return r
    raise ConfigError(f"unknown rule {rule_id} in {stage['name']}")


def active_checks(stage: dict, check_type: str) -> list[dict]:
    """Enabled structured checks of a given type in a stage."""
    return [r["check"] for r in stage.get("rules", [])
            if r.get("enabled", True) and r.get("check") and r["check"].get("type") == check_type]


def validate(cfg: dict) -> None:
    from .toolspecs import TOOL_SPECS

    names = [s["name"] for s in cfg.get("stages", [])]
    if not names:
        raise ConfigError("config needs at least one stage")
    for s in cfg["stages"]:
        for f in ("goal", "allowed_tools", "allowed_node_types", "allowed_edge_types", "rules", "escalation", "apps"):
            if f not in s:
                raise ConfigError(f"stage {s['name']} missing {f}")
        unknown = [t for t in s["allowed_tools"] if t not in TOOL_SPECS]
        if unknown:
            raise ConfigError(f"stage {s['name']}: unknown tools {unknown}")
        for app, level in s["apps"].items():
            if level not in APP_LEVELS:
                raise ConfigError(f"stage {s['name']}: app {app} level {level!r} not in {APP_LEVELS}")
        for r in s["rules"]:
            if not r.get("text"):
                raise ConfigError(f"stage {s['name']}: rule {r.get('id')} has no text")
            if r.get("check") and r["check"].get("type") not in CHECK_TYPES:
                raise ConfigError(f"rule {r['id']}: unknown check type {r['check'].get('type')}")


def _diff_summary(a: dict, b: dict) -> dict:
    """Which stages changed between two configs (kept small for the audit log)."""
    changed = []
    for sa, sb in zip(a.get("stages", []), b.get("stages", [])):
        if sa != sb:
            changed.append(sb["name"])
    return {"changed_stages": changed}


def render_instructions(cfg: dict, version: int) -> str:
    """Render the config as the Markdown instructions given to the Agent Bricks agent.

    The rendered text is a *view* of the config, never edited by hand: a config
    edit re-renders it and updates the Supervisor Agent.
    """
    lines = [
        f"# {cfg['agent_name']} (config v{version})",
        "",
        cfg.get("description", ""),
        "",
        "## How you work",
        "You are ONE agent that works through these stages in order: "
        + ", ".join(s["name"] for s in cfg["stages"]) + ".",
        "- Call `begin_stage` with the stage name before doing anything in that stage.",
        "- Use only the tools allowed in the current stage; other calls are blocked and logged.",
        "- Record every real choice with `record_decision` (options, chosen, rationale).",
        "- Back every claim with `cite_evidence` (event ids or node ids) and finish with `conclude`.",
        "- Pass the run_id you were given to every tool call.",
        f"- Work only on a branch starting with `{cfg['repo']['branch_prefix']}`; never push to "
        + ", ".join(cfg["repo"]["protected_branches"]) + "; open a pull request instead.",
        "",
    ]
    for s in cfg["stages"]:
        lines += [
            f"## Stage: {s['name']}",
            f"**Goal:** {s['goal']}",
            f"**Allowed tools:** {', '.join(s['allowed_tools'])}",
            f"**May traverse node types:** {', '.join(s['allowed_node_types'])}",
            f"**May traverse edge types:** {', '.join(s['allowed_edge_types'])}",
            "**External apps:** " + ", ".join(f"{k}={v}" for k, v in s["apps"].items()),
        ]
        rules = [r for r in s["rules"] if r.get("enabled", True)]
        if rules:
            lines.append("**Rules:**")
            lines += [f"- {r['text']}" for r in rules]
        esc = s["escalation"]
        lines.append(
            f"**Escalation:** a human question times out after {esc.get('ask_timeout_s', 120)}s; then "
            f"{esc.get('on_timeout', 'stop').replace('_', ' ')}. Stop after {esc.get('max_steps', 30)} tool calls"
            + (f" or when: {esc['stop_when']}" if esc.get("stop_when") else "") + "."
        )
        lines.append("")
    return "\n".join(lines)
