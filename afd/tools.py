"""The single tool-wrapper layer (spec section 7 and 9).

Every agent action, whichever host drives the agent (scripted policy or the
Agent Bricks Supervisor Agent calling back into the app), goes through
ToolWrapper.call(). In one place it:

  1. checks the current stage's allowlist (tools, node/edge types, app levels,
     step budget) and the repo's protected branches;
  2. runs the tool against the run's *pinned* ontology version and the repo;
  3. applies structured rule checks (stale owner warnings, no-revisit);
  4. decides whether the event is a milestone (the narrator only narrates
     milestones, and only the wrapper sets this flag, never a model);
  5. emits exactly one semantic event (blocked attempts included).

Tools return plain dicts. Every result carries `event_id` so the agent can
cite it later as evidence.
"""
from __future__ import annotations

import csv
import io
import logging
import re
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .agentcfg import active_checks, stage_by_name
from .channels import HumanChannel, send_message_email
from .ontology import OWNERSHIP_EDGES, Graph
from .repo import ProtectedBranchError
from .toolspecs import TOOL_SPECS

log = logging.getLogger(__name__)

# Node types whose first visit is worth narrating as a milestone.
MILESTONE_NODE_TYPES = {"service", "document", "person", "repo_area", "task"}


@dataclass
class ToolOutcome:
    """What a tool implementation hands back to the wrapper."""
    result: dict
    node_ids: list[str] = field(default_factory=list)
    edge_ids: list[str] = field(default_factory=list)
    focus_node: str | None = None
    status: str = "ok"
    decision: dict | None = None
    evidence_refs: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    external_ids: dict = field(default_factory=dict)
    milestone: bool | None = None  # None = let the wrapper's default rule decide
    actor: str | None = None        # override (a human reply is attributed to the human)
    action_type: str | None = None  # override (a human reply is an "observe" event)


class Blocked(Exception):
    """Raised inside a tool to turn the call into a `blocked` event."""


@dataclass
class RunContext:
    """Everything a run's tools need. One per run."""
    run: dict
    graph: Graph
    config: dict
    repo: Any
    channel: HumanChannel
    emit: Callable[..., dict]           # emit(**event_fields) -> stored event
    today: Any
    gmail_mode: str = "dry_run"
    genie: Callable[[str], dict] | None = None
    stage: str = ""
    branch: str | None = None
    seen_nodes: set = field(default_factory=set)
    traversed_nodes: set = field(default_factory=set)
    stage_steps: dict = field(default_factory=dict)
    # edge_id -> warning text for edges corrected in a recent ontology version
    # (e.g. "was Priya, stale; re-pointed to Marcus in v2 by Dana").
    edge_notes: dict = field(default_factory=dict)
    stop_reason: str | None = None
    concluded: bool = False


class ToolWrapper:
    def __init__(self, ctx: RunContext):
        self.ctx = ctx

    # ------------------------------------------------------------------
    def call(self, name: str, args: dict | None = None, actor: str = "agent") -> dict:
        """Run one tool call through every check and emit its event."""
        args = dict(args or {})
        args.pop("run_id", None)  # LLM hosts pass it; it's implicit here
        ctx = self.ctx
        spec = TOOL_SPECS.get(name)
        started = time.monotonic()

        if spec is None:
            return self._blocked(name, args, f"unknown tool {name!r}", started)

        # begin_stage is how the agent moves between stages; validate the name.
        if name == "begin_stage":
            try:
                stage_by_name(ctx.config, args.get("stage", ""))
            except Exception:
                return self._blocked(name, args, f"unknown stage {args.get('stage')!r}", started)
            ctx.stage = args["stage"]
        elif not ctx.stage:
            return self._blocked(name, args, "call begin_stage before using tools", started)

        stage = stage_by_name(ctx.config, ctx.stage)

        # 1. tool allowlist
        if name not in stage["allowed_tools"]:
            return self._blocked(name, args, f"tool {name} is not allowed in stage {ctx.stage}", started)
        # 2. external app allowlist + level
        if spec["app"]:
            level = stage.get("apps", {}).get(spec["app"], "none")
            need = "read_write" if spec["level"] == "write" else "read"
            if level == "none" or (need == "read_write" and level != "read_write"):
                return self._blocked(name, args, f"app {spec['app']} is '{level}' in stage {ctx.stage}; "
                                                 f"{name} needs '{need}'", started)
        # 3. step budget (escalation/stop condition)
        steps = ctx.stage_steps.get(ctx.stage, 0) + 1
        ctx.stage_steps[ctx.stage] = steps
        if steps > stage["escalation"].get("max_steps", 30):
            ctx.stop_reason = f"step budget of {stage['escalation']['max_steps']} exhausted in {ctx.stage}"
            return self._blocked(name, args, ctx.stop_reason, started)

        # 4. run the tool
        impl = getattr(self, f"_t_{name}")
        try:
            out: ToolOutcome = impl(args, stage)
        except Blocked as b:
            return self._blocked(name, args, str(b), started)
        except ProtectedBranchError as e:
            return self._blocked(name, args, str(e), started)
        except Exception as e:  # tool bug or bad args: an error event, never a crash
            log.exception("tool %s failed", name)
            out = ToolOutcome(result={"error": f"{type(e).__name__}: {e}"}, status="error")

        milestone = out.milestone if out.milestone is not None else self._default_milestone(name, out)
        if out.warnings or out.status in ("error", "timeout"):
            milestone = True
        for nid in out.node_ids:
            ctx.seen_nodes.add(nid)

        event = ctx.emit(stage=ctx.stage, actor=out.actor or actor,
                         action_type=out.action_type or spec["action_type"], tool=name,
                         node_ids=out.node_ids, edge_ids=out.edge_ids, focus_node=out.focus_node,
                         input=args, result=out.result, status=out.status,
                         latency_ms=int((time.monotonic() - started) * 1000), decision=out.decision,
                         evidence_refs=out.evidence_refs, milestone=milestone,
                         external_ids=out.external_ids, warnings=out.warnings)
        return {"event_id": event["event_id"], "status": out.status, **out.result,
                **({"warnings": out.warnings} if out.warnings else {})}

    # ------------------------------------------------------------------
    def _blocked(self, name: str, args: dict, reason: str, started: float) -> dict:
        """Blocked attempts are first-class events (and always milestones)."""
        ev = self.ctx.emit(stage=self.ctx.stage or "-", actor="agent", action_type="blocked", tool=name,
                           input=args, result={"reason": reason}, status="blocked",
                           latency_ms=int((time.monotonic() - started) * 1000), milestone=True)
        return {"event_id": ev["event_id"], "status": "blocked", "reason": reason}

    def _default_milestone(self, name: str, out: ToolOutcome) -> bool:
        """The wrapper's milestone rule (documented in README > Narration)."""
        if name in ("begin_stage", "record_decision", "ask_human", "edit_file", "github_create_branch",
                    "github_open_pr", "message_human", "conclude", "analyze_csv", "genie_query"):
            return True
        if name in ("ontology_lookup", "ontology_traverse"):
            new = [n for n in out.node_ids if n not in self.ctx.seen_nodes]
            return any((self.ctx.graph.node(n) or {}).get("type") in MILESTONE_NODE_TYPES for n in new)
        return False

    def _check_node_allowed(self, node_id: str, stage: dict) -> dict:
        node = self.ctx.graph.node(node_id)
        if node is None:
            raise ValueError(f"unknown node {node_id}")
        if node["type"] not in stage["allowed_node_types"]:
            raise Blocked(f"node type {node['type']} is not traversable in stage {stage['name']}")
        return node

    def _edge_view(self, edge: dict, other: dict, stage: dict) -> tuple[dict, list[str]]:
        """Edge + provenance for results, plus any rule warnings it triggers."""
        g, today = self.ctx.graph, self.ctx.today
        age = Graph.edge_age_days(edge, today)
        view = {"edge_id": edge["id"], "type": edge["type"], "src": edge["src"], "dst": edge["dst"],
                "node": {"id": other["id"], "type": other["type"], "label": other["label"]},
                "describe": g.describe_edge(edge), "source": edge["source"],
                "confidence": edge["confidence"], "last_verified": edge["last_verified"], "age_days": age}
        warnings = []
        if edge["id"] in self.ctx.edge_notes:
            warnings.append(self.ctx.edge_notes[edge["id"]])
        src_type = (g.node(edge["src"]) or {}).get("type")
        if edge["type"] in OWNERSHIP_EDGES and src_type == "person":
            for chk in active_checks(stage, "stale_owner"):
                limit = int(chk.get("max_age_days", 180))
                if age > limit:
                    warnings.append(f"Stale owner: '{view['describe']}' was last verified {edge['last_verified']} "
                                    f"({age} days ago, limit {limit}). Confirm with a second source before relying on it.")
            for chk in active_checks(stage, "min_confidence"):
                if edge["confidence"] < float(chk.get("min_confidence", 0.7)):
                    warnings.append(f"Low confidence: '{view['describe']}' has confidence {edge['confidence']}.")
        return view, warnings

    # ------------------------------------------------------------------
    # Tool implementations. Each returns a ToolOutcome.
    # ------------------------------------------------------------------
    def _t_begin_stage(self, args: dict, stage: dict) -> ToolOutcome:
        return ToolOutcome(result={"stage": stage["name"], "goal": stage["goal"],
                                   "allowed_tools": stage["allowed_tools"],
                                   "rules": [r["text"] for r in stage["rules"] if r.get("enabled", True)]})

    def _t_ontology_lookup(self, args: dict, stage: dict) -> ToolOutcome:
        g = self.ctx.graph
        query = str(args.get("query", ""))
        types = [t for t in (args.get("types") or stage["allowed_node_types"]) if t in stage["allowed_node_types"]]
        hits = [g.node(query)] if g.node(query) and g.node(query)["type"] in types else g.search(query, types)
        hits = hits[:5]
        return ToolOutcome(result={"nodes": [{"id": n["id"], "type": n["type"], "label": n["label"],
                                              "props": n.get("props", {})} for n in hits]},
                           node_ids=[n["id"] for n in hits], focus_node=hits[0]["id"] if hits else None)

    def _t_ontology_traverse(self, args: dict, stage: dict) -> ToolOutcome:
        ctx = self.ctx
        node_id = args["node_id"]
        self._check_node_allowed(node_id, stage)
        requested = args.get("edge_types") or stage["allowed_edge_types"]
        bad = [t for t in requested if t not in stage["allowed_edge_types"]]
        if bad:
            raise Blocked(f"edge types {bad} are not traversable in stage {stage['name']}")
        # Rule: no_revisit -> refuse to traverse out of a node twice in one run.
        if active_checks(stage, "no_revisit") and (node_id, args.get("direction", "both")) in ctx.traversed_nodes:
            raise Blocked(f"already traversed {node_id} in this run (rule: never revisit; escalate instead)")
        ctx.traversed_nodes.add((node_id, args.get("direction", "both")))
        views, warnings, node_ids, edge_ids = [], [], [node_id], []
        for edge, other in ctx.graph.neighbors(node_id, requested, args.get("direction", "both")):
            if other["type"] not in stage["allowed_node_types"]:
                continue  # silently outside this stage's view of the graph
            view, w = self._edge_view(edge, other, stage)
            views.append(view)
            warnings += w
            node_ids.append(other["id"])
            edge_ids.append(edge["id"])
        return ToolOutcome(result={"node": {"id": node_id, "label": ctx.graph.label(node_id)},
                                   "neighbors": views, "count": len(views)},
                           node_ids=node_ids, edge_ids=edge_ids, focus_node=node_id, warnings=warnings)

    def _t_record_decision(self, args: dict, stage: dict) -> ToolOutcome:
        decision = {"options": list(args.get("options", [])), "chosen": args.get("chosen", ""),
                    "rationale": args.get("rationale", "")}
        node_ids = list(args.get("node_ids") or [])
        return ToolOutcome(result={"recorded": True}, decision=decision, node_ids=node_ids,
                           edge_ids=list(args.get("edge_ids") or []),
                           focus_node=node_ids[0] if node_ids else None,
                           evidence_refs=list(args.get("evidence_refs") or []))

    def _t_cite_evidence(self, args: dict, stage: dict) -> ToolOutcome:
        refs = list(args.get("evidence_refs") or [])
        nodes = [r for r in refs if self.ctx.graph.node(r)]
        edges = [r for r in refs if self.ctx.graph.edge(r)]
        return ToolOutcome(result={"claim": args.get("claim", ""), "evidence_refs": refs},
                           evidence_refs=refs, node_ids=nodes, edge_ids=edges)

    def _t_ask_human(self, args: dict, stage: dict) -> ToolOutcome:
        """Pause the run: emit a 'waiting' event, block for the reply, emit the reply."""
        ctx = self.ctx
        person = ctx.graph.node(args["person_id"])
        if not person or person["type"] != "person":
            raise ValueError(f"{args['person_id']} is not a person in the ontology")
        edge_ids = list(args.get("edge_ids") or [])
        related = [n for e in edge_ids if (ed := ctx.graph.edge(e)) for n in (ed["src"], ed["dst"])]
        node_ids = [person["id"]] + [n for n in related if n != person["id"]]
        timeout = float(stage["escalation"].get("ask_timeout_s", 120))
        mirror = stage.get("apps", {}).get("gmail") == "read_write"
        # The question row needs the seq of the event that asks; reserve it by emitting first.
        q = ctx.channel.ask(run_id=ctx.run["run_id"], seq=-1, person=person, question=args["question"],
                            mirror_email=mirror)
        ask_ev = ctx.emit(stage=ctx.stage, actor="agent", action_type="ask_human", tool="ask_human",
                          node_ids=node_ids, edge_ids=edge_ids, focus_node=person["id"], input=args,
                          result={"question_id": q["question_id"], "to": person["label"], "timeout_s": timeout},
                          status="waiting", milestone=True,
                          external_ids={"gmail_message_id": q["email"]["message_id"]} if q.get("email") else {})
        ctx.channel.store.update("questions", "question_id", q["question_id"], {"seq": ask_ev["seq"]})
        final = ctx.channel.wait(q["question_id"], timeout)
        if final["status"] == "answered":
            # The reply is its own observed event, attributed to the human.
            return ToolOutcome(result={"question_id": q["question_id"], "reply": final["reply"],
                                       "answered_by": final["answered_by"], "asked_event": ask_ev["event_id"]},
                               node_ids=node_ids, edge_ids=edge_ids, focus_node=person["id"],
                               evidence_refs=[ask_ev["event_id"]], milestone=True,
                               actor=f"human:{person['id']}", action_type="observe")
        return ToolOutcome(result={"question_id": q["question_id"], "reply": None, "asked_event": ask_ev["event_id"],
                                   "escalation": stage["escalation"].get("on_timeout", "stop")},
                           node_ids=node_ids, edge_ids=edge_ids, focus_node=person["id"], status="timeout")

    def _t_message_human(self, args: dict, stage: dict) -> ToolOutcome:
        person = self.ctx.graph.node(args["person_id"])
        if not person or person["type"] != "person":
            raise ValueError(f"{args['person_id']} is not a person in the ontology")
        email = send_message_email(person, args.get("text", ""), self.ctx.gmail_mode)
        return ToolOutcome(result={"to": person["label"], "text": args.get("text", ""), "email": email},
                           node_ids=[person["id"]], focus_node=person["id"],
                           external_ids={"gmail_message_id": email["message_id"]} if email else {})

    def _repo_node_for(self, path: str) -> str | None:
        """Map a file path to the repo_area node whose path prefixes it."""
        best = None
        for n in self.ctx.graph.nodes.values():
            p = n.get("props", {}).get("path")
            if n["type"] == "repo_area" and p and path.startswith(p) and (best is None or len(p) > len(best[1])):
                best = (n["id"], p)
        if best:
            return best[0]
        # documents can also map to a file
        for n in self.ctx.graph.nodes.values():
            if n["type"] == "document" and n.get("props", {}).get("path") == path:
                return n["id"]
        return None

    def _read_branch(self) -> str:
        return self.ctx.branch if self.ctx.branch and self.ctx.branch in self.ctx.repo.branches() else "main"

    def _t_read_file(self, args: dict, stage: dict) -> ToolOutcome:
        branch = self._read_branch()
        content = self.ctx.repo.read(args["path"], branch=branch)
        anchor = self._repo_node_for(args["path"])
        return ToolOutcome(result={"path": args["path"], "branch": branch, "content": content},
                           node_ids=[anchor] if anchor else [], focus_node=anchor)

    def _t_edit_file(self, args: dict, stage: dict) -> ToolOutcome:
        branch = args.get("branch") or self.ctx.branch
        if not branch:
            raise Blocked("no working branch yet: call github_create_branch first")
        res = self.ctx.repo.write(args["path"], args["content"], branch=branch)
        anchor = self._repo_node_for(args["path"])
        return ToolOutcome(result=res, node_ids=[anchor] if anchor else [], focus_node=anchor)

    def _t_analyze_csv(self, args: dict, stage: dict) -> ToolOutcome:
        text = self.ctx.repo.read(args["path"], branch=self._read_branch())
        rows = list(csv.DictReader(io.StringIO(text)))
        cols = list(rows[0].keys()) if rows else []
        profile = {}
        for c in cols:
            vals = [r[c] for r in rows]
            nums = []
            for v in vals:
                try:
                    nums.append(float(v))
                except ValueError:
                    pass
            if nums and len(nums) == len(vals):
                profile[c] = {"kind": "numeric", "min": min(nums), "max": max(nums),
                              "mean": round(statistics.mean(nums), 2)}
            else:
                counts: dict[str, int] = {}
                for v in vals:
                    counts[v] = counts.get(v, 0) + 1
                profile[c] = {"kind": "text", "distinct": len(counts),
                              "top": sorted(counts.items(), key=lambda kv: -kv[1])[:3]}
        anchor = self._repo_node_for(args["path"])
        return ToolOutcome(result={"path": args["path"], "rows": len(rows), "columns": cols, "profile": profile},
                           node_ids=[anchor] if anchor else [], focus_node=anchor)

    def _t_web_search(self, args: dict, stage: dict) -> ToolOutcome:
        # Offline stand-in. Under Agent Bricks, the supervisor's own
        # databricks_web_search tool is used instead (see afd/agentbricks.py).
        return ToolOutcome(result={"query": args.get("query", ""), "simulated": True, "results": [
            {"title": "RFC 7519 - JSON Web Token (JWT)", "url": "https://www.rfc-editor.org/rfc/rfc7519"},
            {"title": "OWASP Key Management Cheat Sheet",
             "url": "https://cheatsheetseries.owasp.org/cheatsheets/Key_Management_Cheat_Sheet.html"},
        ]})

    def _t_genie_query(self, args: dict, stage: dict) -> ToolOutcome:
        if not self.ctx.genie:
            return ToolOutcome(result={"available": False, "reason": "no Genie space configured (AFD_GENIE_SPACE_ID)"},
                               status="error")
        res = self.ctx.genie(args["question"])
        return ToolOutcome(result=res, external_ids={"genie_conversation_id": res.get("conversation_id", "")})

    def _t_github_create_branch(self, args: dict, stage: dict) -> ToolOutcome:
        prefix = self.ctx.config["repo"]["branch_prefix"]
        name = args["name"]
        if not name.startswith(prefix):
            raise Blocked(f"branch {name!r} must start with {prefix!r}")
        if name in self.ctx.config["repo"]["protected_branches"]:
            raise Blocked(f"{name} is protected")
        res = self.ctx.repo.create_branch(name)
        self.ctx.branch = name
        return ToolOutcome(result=res, node_ids=[], external_ids={"github_branch": name})

    def _t_github_open_pr(self, args: dict, stage: dict) -> ToolOutcome:
        if not self.ctx.branch:
            raise Blocked("no working branch: call github_create_branch first")
        pr = self.ctx.repo.open_pr(self.ctx.branch, args["title"], args.get("body", ""), author="agent")
        anchors = sorted({a for f in pr.get("files", []) if (a := self._repo_node_for(f["path"]))})
        return ToolOutcome(result={"number": pr["number"], "url": pr["url"], "branch": self.ctx.branch,
                                   "files": pr.get("files", [])},
                           node_ids=anchors, focus_node=anchors[0] if anchors else None,
                           external_ids={"github_pr": pr["url"]})

    def _t_conclude(self, args: dict, stage: dict) -> ToolOutcome:
        claims = list(args.get("claims") or [])
        unsupported = [c.get("text", "") for c in claims if not c.get("evidence_refs")]
        if unsupported and active_checks(stage, "require_evidence"):
            raise Blocked(f"claims without evidence (rule: cite evidence for every claim): {unsupported}")
        refs = sorted({r for c in claims for r in c.get("evidence_refs", [])})
        self.ctx.concluded = True
        return ToolOutcome(result={"answer": args.get("answer", ""), "claims": claims},
                           evidence_refs=refs, node_ids=[r for r in refs if self.ctx.graph.node(r)],
                           edge_ids=[r for r in refs if self.ctx.graph.edge(r)])


def slug(text: str) -> str:
    """Filesystem/branch-safe slug."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]
