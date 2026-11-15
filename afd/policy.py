"""The scripted agent policy: a deterministic host for the staged agent.

Why a scripted host exists next to Agent Bricks:
  * The demo must be reproducible on stage and offline.
  * Tests and the benchmark need deterministic behavior to compare
    conditions (no ontology / ontology / ontology after an edit).

It is NOT a mock that fakes events: it calls the same tools through the same
ToolWrapper as the LLM host, so every event, allowlist check and flag is real.
It also *reads the agent config*: it acts on the structured rule checks
(stale_owner, fallback_repo_owner, no_revisit) exactly as written, which is
why accepting the AI-proposed rule visibly changes the next run's path.

Stages (spec section 4): triage -> investigate -> act -> verify.
Two task kinds:
  build    "build the backend for the invoicing feature"  -> all four stages
  question "who should I ask about the data retention policy?" -> no act stage
"""
from __future__ import annotations

import re

from .agentcfg import active_checks, stage_by_name
from .ontology import Graph
from .tools import RunContext, ToolWrapper, slug

HOP_BUDGET = 8  # how far owner resolution walks depends_on before giving up


class ScriptedPolicy:
    def __init__(self, ctx: RunContext, wrapper: ToolWrapper):
        self.ctx, self.w = ctx, wrapper
        self.task: str = ctx.run["task"]
        self.kind = "question" if re.match(r"\s*(who|whom|which person|which teammate)\b", self.task, re.I) else "build"
        # Facts gathered along the way, with the event ids that support them.
        self.task_node: dict | None = None
        self.service: dict | None = None
        self.target: dict | None = None
        self.owner: dict | None = None            # {"person", "edge_id", "decision_event"}
        self.answer: dict | None = None           # {"text", "event_id", "ask_event"}
        self.requirements_event: str | None = None
        self.pr: dict | None = None
        self.pr_event: str | None = None
        self.outcome: str = "unknown"             # benchmark-visible: asked:<pid> | escalated | dead_end ...

    # -- plumbing ---------------------------------------------------------------
    def call(self, tool: str, **args) -> dict:
        return self.w.call(tool, args)

    def has_tool(self, stage: str, tool: str) -> bool:
        try:
            return tool in stage_by_name(self.ctx.config, stage)["allowed_tools"]
        except Exception:
            return False

    def stopped(self) -> bool:
        return self.ctx.stop_reason is not None

    # -- entry point ------------------------------------------------------------
    def run(self) -> None:
        stages = [s["name"] for s in self.ctx.config["stages"]]
        plan = [s for s in ("triage", "investigate", "act", "verify") if s in stages]
        if self.kind == "question":
            plan = [s for s in plan if s != "act"]
        for stage in plan:
            if self.stopped():
                break
            self.call("begin_stage", stage=stage)
            getattr(self, f"stage_{stage}")()
        if not self.ctx.concluded and "verify" in stages:
            # Make sure every run ends with an explicit conclusion, even a failed one.
            if self.ctx.stage != "verify":
                self.call("begin_stage", stage="verify")
            self.call("conclude", answer=f"Stopped: {self.ctx.stop_reason or self.outcome}",
                      claims=[{"text": f"The run stopped ({self.ctx.stop_reason or self.outcome}).",
                               "evidence_refs": [self._last_event_id()]}])

    def _last_event_id(self) -> str:
        return f"{self.ctx.run['run_id']}:{self.ctx.run.get('_last_seq', 0)}"

    # -- triage -----------------------------------------------------------------
    def stage_triage(self) -> None:
        if not self.has_tool("triage", "ontology_lookup"):
            # "No ontology" condition: the agent only has the task text.
            self.call("record_decision", options=["use the task text only"], chosen="use the task text only",
                      rationale="No ontology tools are enabled for this agent configuration.")
            return
        if self.kind == "build":
            hits = self.call("ontology_lookup", query=self.task, types=["task", "ticket"])["nodes"]
            if not hits:
                self.outcome = "dead_end"
                self.ctx.stop_reason = "the task does not match any task or ticket node"
                self.call("record_decision", options=[], chosen="stop", rationale=self.ctx.stop_reason)
                return
            self.task_node = hits[0]
            t = self.call("ontology_traverse", node_id=hits[0]["id"], edge_types=["affects"], direction="out")
            services = [n for n in t.get("neighbors", []) if n["node"]["type"] == "service"]
            if not services:
                self.outcome = "dead_end"
                self.ctx.stop_reason = f"{hits[0]['label']} does not affect any service"
                return
            svc = max(services, key=lambda n: n["confidence"])
            self.service = svc["node"]
            self.call("record_decision",
                      options=[h["label"] for h in hits[:3]], chosen=hits[0]["label"],
                      rationale=(f"The task text best matches '{hits[0]['label']}', which affects "
                                 f"{svc['node']['label']} ({svc['source']}, confidence {svc['confidence']})."),
                      node_ids=[hits[0]["id"], svc["node"]["id"]], edge_ids=[svc["edge_id"]])
        else:
            hits = self.call("ontology_lookup", query=self.task)["nodes"]
            if not hits:
                self.outcome = "dead_end"
                self.ctx.stop_reason = "the question does not match any node"
                return
            self.target = hits[0]
            self.call("record_decision", options=[h["label"] for h in hits[:3]], chosen=hits[0]["label"],
                      rationale=f"The question is about '{hits[0]['label']}'.", node_ids=[hits[0]["id"]])

    # -- investigate ------------------------------------------------------------
    def stage_investigate(self) -> None:
        if not self.has_tool("investigate", "ontology_traverse"):
            if self.has_tool("investigate", "genie_query"):
                return self._investigate_with_genie()
            return self._investigate_without_ontology()
        if self.kind == "build":
            if not self.service:
                return
            deps = self.call("ontology_traverse", node_id=self.service["id"], edge_types=["depends_on"], direction="out")
            docs = [n for n in deps.get("neighbors", []) if n["node"]["type"] == "document"]
            for d in docs:
                node = self.ctx.graph.node(d["node"]["id"])
                if node["props"].get("readable") and node["props"].get("path"):
                    r = self.call("read_file", path=node["props"]["path"])
                    if r["status"] == "ok":
                        self.requirements_event = r["event_id"]
            # The unreadable document (Security specs) needs a person.
            needs_person = [d for d in docs if not self.ctx.graph.node(d["node"]["id"])["props"].get("readable")]
            if needs_person:
                self.target = self.ctx.graph.node(needs_person[0]["node"]["id"])
        if self.target and not self.stopped():
            self._find_owner_and_ask(self.target)

    def _find_owner_and_ask(self, target: dict) -> None:
        owner = self.resolve_owner(target["id"])
        if not owner:
            return
        topic = target["label"]
        question = (f"I'm working on \"{self.task}\". What do the {topic} require here? "
                    f"(You are listed as responsible for {topic}.)")
        if "security" in topic.lower():
            question = (f"I'm building the invoicing backend. What do the security specs require for service "
                        f"auth tokens and key rotation?")
        if "retention" in topic.lower():
            question = "What does the data retention policy require for invoices?"
        if "api" in topic.lower():
            question = "Which API guidelines should the new endpoints follow?"
        self._ask(owner, question, target)

    def _ask(self, owner: dict, question: str, target: dict, escalated: bool = False) -> None:
        r = self.call("ask_human", person_id=owner["person"], question=question, edge_ids=[owner["edge_id"]])
        if r["status"] == "ok":
            self.answer = {"text": r.get("reply", ""), "event_id": r["event_id"], "ask_event": r.get("asked_event")}
            self.outcome = f"asked:{owner['person']}"
            self.call("cite_evidence",
                      claim=f"{self.ctx.graph.label(owner['person'])} described what {target['label']} require.",
                      evidence_refs=[r["event_id"], owner["edge_id"]])
            return
        if r["status"] == "timeout":
            stage = stage_by_name(self.ctx.config, self.ctx.stage)
            if stage["escalation"].get("on_timeout") == "escalate_to_second_source" and not escalated:
                alt = self.second_source(target["id"], exclude={owner["person"]})
                if alt:
                    self.call("record_decision",
                              options=[self.ctx.graph.label(owner["person"]), self.ctx.graph.label(alt["person"])],
                              chosen=self.ctx.graph.label(alt["person"]),
                              rationale=f"{self.ctx.graph.label(owner['person'])} did not reply in time; escalating to "
                                        f"the second source.", node_ids=[alt["person"]], edge_ids=[alt["edge_id"]])
                    return self._ask(alt, question, target, escalated=True)
            self.outcome = "needs_human"
            self.ctx.stop_reason = f"no reply from {self.ctx.graph.label(owner['person'])} within the timeout"
        else:
            self.outcome = "blocked"

    # -- owner resolution (shared by build + question tasks) ---------------------
    def resolve_owner(self, target_id: str) -> dict | None:
        """Walk the ontology to the person accountable for `target_id`.

        Baseline behavior (no rules): trust the most confident `owns` edge; if
        nothing owns the target, follow its `depends_on` edges and try again.
        Rules from the config change this:
          stale_owner          stale owner edge -> prefer a fresh direct owner, else a second source
          fallback_repo_owner  also accept `responsible_for` (e.g. CODEOWNERS on a repo area)
          no_revisit           the wrapper blocks re-traversal; we escalate instead of looping
        """
        stage = stage_by_name(self.ctx.config, self.ctx.stage)
        stale_rules = active_checks(stage, "stale_owner")
        fallback = bool(active_checks(stage, "fallback_repo_owner"))
        owner_types = ["owns", "responsible_for"] if fallback else ["owns"]
        g, today = self.ctx.graph, self.ctx.today
        frontier, hops = [target_id], 0
        while frontier and hops < HOP_BUDGET and not self.stopped():
            node_id = frontier.pop(0)
            hops += 1
            r = self.call("ontology_traverse", node_id=node_id, edge_types=owner_types, direction="in")
            if r["status"] == "blocked":
                return self._escalate(f"the path revisits {g.label(node_id)} (loop); escalating to the requester")
            cands = [n for n in r.get("neighbors", []) if n["node"]["type"] == "person"]
            if cands:
                cands.sort(key=lambda n: (-n["confidence"], n["age_days"]))
                chosen, why = cands[0], "the most confident owner edge"
                options = [c["node"]["label"] for c in cands]
                if stale_rules:
                    limit = int(stale_rules[0].get("max_age_days", 180))
                    if chosen["age_days"] > limit:
                        fresh = [c for c in cands if c["age_days"] <= limit]
                        if fresh:
                            chosen, why = fresh[0], f"the top edge is {cands[0]['age_days']} days old; this one is fresh"
                        else:
                            alt = self.second_source(node_id, exclude=set())
                            if alt:
                                self.call("record_decision",
                                          options=options + [g.label(alt["person"])], chosen=g.label(alt["person"]),
                                          rationale=(f"'{cands[0]['describe']}' was last verified {cands[0]['age_days']} "
                                                     f"days ago (limit {limit}); a fresh second source "
                                                     f"({alt['describe']}) names {g.label(alt['person'])}."),
                                          node_ids=[alt["person"], node_id], edge_ids=[alt["edge_id"], cands[0]["edge_id"]])
                                self.owner = alt
                                return alt
                            why = f"no second source found; {cands[0]['node']['label']} is unconfirmed"
                self.call("record_decision", options=options, chosen=chosen["node"]["label"],
                          rationale=f"{chosen['describe']} ({chosen['source']}, confidence {chosen['confidence']}, "
                                    f"verified {chosen['last_verified']}): {why}.",
                          node_ids=[chosen["node"]["id"], node_id], edge_ids=[chosen["edge_id"]])
                self.owner = {"person": chosen["node"]["id"], "edge_id": chosen["edge_id"], "describe": chosen["describe"]}
                return self.owner
            # Nobody owns this node: look at what it depends on.
            d = self.call("ontology_traverse", node_id=node_id, edge_types=["depends_on"], direction="out")
            if d["status"] == "blocked":
                return self._escalate(f"the path revisits {g.label(node_id)} (loop); escalating to the requester")
            frontier += [n["node"]["id"] for n in d.get("neighbors", [])]
        if hops >= HOP_BUDGET:
            self.outcome = "budget_exhausted"
            self.call("record_decision", options=[], chosen="give up",
                      rationale=f"walked {hops} hops without finding an owner")
            return None
        return self._escalate(f"nothing in the ontology says who owns {g.label(target_id)}")

    def _escalate(self, reason: str) -> None:
        self.outcome = "escalated"
        self.call("record_decision", options=["guess an owner", "escalate to the requester"],
                  chosen="escalate to the requester", rationale=reason)
        return None

    def second_source(self, node_id: str, exclude: set) -> dict | None:
        """A fresh, independent source for who is accountable for `node_id`:
        the person responsible_for the repo area the node lives in."""
        g, today = self.ctx.graph, self.ctx.today
        r = self.call("ontology_traverse", node_id=node_id, edge_types=["depends_on"], direction="out")
        for area in [n for n in r.get("neighbors", []) if n["node"]["type"] == "repo_area"]:
            rr = self.call("ontology_traverse", node_id=area["node"]["id"], edge_types=["responsible_for"], direction="in")
            people = [n for n in rr.get("neighbors", []) if n["node"]["type"] == "person"
                      and n["node"]["id"] not in exclude]
            if people:
                best = max(people, key=lambda n: (n["confidence"], -n["age_days"]))
                return {"person": best["node"]["id"], "edge_id": best["edge_id"], "describe": best["describe"]}
        return None

    def _investigate_with_genie(self) -> None:
        """Genie-only condition: ask the Genie space who owns the topic."""
        r = self.call("genie_query", question=(f"{self.task} Using the ontology edges table, which person owns or "
                                               f"is responsible for it? Answer with the person's full name."))
        if r["status"] != "ok":
            self.outcome = "escalated"
            return
        haystack = (str(r.get("text", "")) + " " + str(r.get("rows", ""))).lower()
        people = [n for n in self.ctx.graph.nodes.values() if n["type"] == "person"]
        hits = [p for p in people if p["label"].lower() in haystack or p["id"] in haystack]
        # The person Genie names first in its answer is its answer.
        hits.sort(key=lambda p: min(i for i in (haystack.find(p["label"].lower()), haystack.find(p["id"])) if i >= 0))
        if not hits:
            self.outcome = "escalated"
            self.call("record_decision", options=[], chosen="escalate to the requester",
                      rationale="Genie's answer did not name anyone.", evidence_refs=[r["event_id"]])
            return
        person = hits[0]
        self.call("record_decision", options=[p["label"] for p in hits], chosen=person["label"],
                  rationale="Genie named this person.", evidence_refs=[r["event_id"]], node_ids=[person["id"]])
        self.owner = {"person": person["id"], "edge_id": "", "describe": "named by Genie"}
        q = self.call("ask_human", person_id=person["id"], question=f"Question about: {self.task}", edge_ids=[])
        if q["status"] == "ok":
            self.answer = {"text": q.get("reply", ""), "event_id": q["event_id"], "ask_event": q.get("asked_event")}
        self.outcome = f"asked:{person['id']}"

    def _investigate_without_ontology(self) -> None:
        """No-ontology condition: rely on the (stale) team wiki page."""
        if not self.has_tool("investigate", "read_file"):
            self.outcome = "escalated"
            return
        r = self.call("read_file", path="docs/team_wiki.md")
        words = {w for w in re.findall(r"[a-z]+", self.task.lower()) if len(w) > 3}
        best = None
        for line in r.get("content", "").splitlines():
            m = re.match(r"- (.+?) - (\w+)", line)
            if m and words & set(re.findall(r"[a-z]+", m.group(1).lower())):
                best = (m.group(1), m.group(2))
                break
        if not best:
            self.outcome = "escalated"
            self.call("record_decision", options=[], chosen="escalate to the requester",
                      rationale="The team wiki does not list anyone for this topic.")
            return
        topic, first = best
        person_id = "p_" + first.lower()
        self.call("record_decision", options=[first], chosen=first,
                  rationale=f"The team wiki lists {first} for {topic}.", evidence_refs=[r["event_id"]])
        if self.ctx.graph.node(person_id) and self.has_tool("investigate", "ask_human"):
            self.owner = {"person": person_id, "edge_id": "", "describe": f"team wiki: {topic} - {first}"}
            q = self.call("ask_human", person_id=person_id, question=f"What do I need to know about {topic}?",
                          edge_ids=[])
            if q["status"] == "ok":
                self.answer = {"text": q.get("reply", ""), "event_id": q["event_id"], "ask_event": q.get("asked_event")}
        self.outcome = f"asked:{person_id}"

    # -- act --------------------------------------------------------------------
    def stage_act(self) -> None:
        if not self.service:
            return
        task_id = (self.task_node or {}).get("id", slug(self.task))
        branch = f"{self.ctx.config['repo']['branch_prefix']}{task_id}-{self.ctx.run['run_id'][-6:]}"
        if self.call("github_create_branch", name=branch)["status"] != "ok":
            return
        csv = self.call("analyze_csv", path="finance/invoices_sample.csv")
        guidance = (self.answer or {}).get("text") or ""
        alg = "RS256" if "rs256" in guidance.lower() else "HS256" if "hs256" in guidance.lower() else "RS256"
        m = re.search(r"(\d+)\s*days", guidance)
        rotation = int(m.group(1)) if m else 90
        who = self.ctx.graph.label(self.owner["person"]) if self.owner else "nobody"
        self.call("record_decision",
                  options=["HS256 tokens with a shared secret", "RS256 tokens signed by the auth service"],
                  chosen=("RS256 tokens signed by the auth service" if alg == "RS256" else "HS256 tokens with a shared secret"),
                  rationale=f"Follow the security guidance from {who} ({alg}, {rotation}-day key rotation).",
                  evidence_refs=[e for e in [(self.answer or {}).get("event_id")] if e],
                  node_ids=["svc_invoicing"])
        self.call("edit_file", path="services/invoicing/security.py",
                  content=_security_module(alg, rotation, who, (self.answer or {}).get("event_id", "")))
        self.call("edit_file", path="services/invoicing/app.py", content=_app_module(csv.get("columns") or []))
        self.call("edit_file", path="services/invoicing/test_invoices.py", content=_test_module())
        body = (f"Implements the invoicing backend.\n\n- Security: {alg} service tokens, {rotation}-day key rotation "
                f"(guidance from {who}, event {(self.answer or {}).get('event_id', 'n/a')}).\n"
                f"- Data model from docs/invoice_requirements.md (event {self.requirements_event}).\n"
                f"- Opened by the Lumen build agent from run {self.ctx.run['run_id']}.")
        pr = self.call("github_open_pr", title="Invoicing backend (agent)", body=body)
        if pr["status"] == "ok":
            self.pr, self.pr_event = pr, pr["event_id"]
            requester = self.ctx.run.get("requester")
            if requester and self.ctx.graph.node(requester):
                self.call("message_human", person_id=requester,
                          text=f"PR #{pr['number']} is open for \"{self.task}\" on {branch}.")

    # -- verify -----------------------------------------------------------------
    def stage_verify(self) -> None:
        g = self.ctx.graph
        claims = []
        if self.kind == "build":
            if self.pr:
                self.call("read_file", path="services/invoicing/security.py")
                claims.append({"text": f"The invoicing backend is on branch {self.pr['branch']} in pull request "
                                       f"#{self.pr['number']}.", "evidence_refs": [self.pr_event]})
            if self.answer and self.owner:
                person = g.label(self.owner["person"])
                claims.append({"text": f"Token signing follows {person}'s security guidance.",
                               "evidence_refs": [self.answer["event_id"]]})
                claims.append({"text": f"{person} was asked because the ontology lists them as accountable for "
                                       f"{self.target['label'] if self.target else 'the security specs'}.",
                               "evidence_refs": [e for e in [self.owner.get("edge_id")] if e]})
            if self.requirements_event:
                claims.append({"text": "The data model follows the invoice requirements document.",
                               "evidence_refs": [self.requirements_event]})
            answer = (f"Opened PR #{self.pr['number']} for the invoicing backend." if self.pr
                      else f"Could not finish: {self.ctx.stop_reason or self.outcome}.")
        else:
            if self.owner and self.outcome.startswith("asked:"):
                person = g.label(self.owner["person"])
                answer = f"Ask {person} about {(self.target or {}).get('label', 'this')}."
                claims.append({"text": f"{person} is accountable for {(self.target or {}).get('label', 'this topic')}.",
                               "evidence_refs": [e for e in [self.owner.get("edge_id"), (self.answer or {}).get("event_id")] if e]})
            else:
                answer = "No accountable person found; escalated to the requester."
                claims.append({"text": "The ontology does not identify an owner.",
                               "evidence_refs": [self._last_event_id()]})
        for c in claims:
            if c["evidence_refs"]:
                self.call("cite_evidence", claim=c["text"], evidence_refs=c["evidence_refs"])
        self.call("conclude", answer=answer, claims=claims)


# -- code the agent writes (kept small; it's the artifact, not the product) --------
def _security_module(alg: str, rotation_days: int, who: str, evidence: str) -> str:
    if alg == "HS256":
        key_line = "SHARED_SECRET_ENV = 'INVOICING_TOKEN_SECRET'  # HS256 uses a shared secret"
    else:
        key_line = ("AUTH_SERVICE_JWKS_URL = 'https://auth.internal/.well-known/jwks.json'"
                    "  # RS256: verify with the auth service's public keys")
    return f'''"""Service-token signing for the invoicing service.

Generated by the Lumen build agent. Policy source: {who} (evidence {evidence}).
"""
from datetime import timedelta

TOKEN_ALGORITHM = "{alg}"
KEY_ROTATION = timedelta(days={rotation_days})
{key_line}


def token_settings() -> dict:
    return {{"algorithm": TOKEN_ALGORITHM, "rotation_days": KEY_ROTATION.days}}
'''


def _app_module(columns: list[str]) -> str:
    cols = ", ".join(columns) or "invoice_id, customer_id, amount_cents, currency, status, due_date"
    return f'''"""Invoicing service API (generated by the Lumen build agent).

Fields follow docs/invoice_requirements.md; sample columns: {cols}.
"""
from dataclasses import dataclass, field

STATUSES = ("draft", "sent", "paid", "overdue", "void")


@dataclass
class Invoice:
    invoice_id: str
    customer_id: str
    currency: str
    due_date: str
    status: str = "draft"
    line_items: list = field(default_factory=list)  # [(description, amount_cents)]

    @property
    def amount_cents(self) -> int:
        return sum(amount for _, amount in self.line_items)


_DB: dict[str, Invoice] = {{}}


def create(inv: Invoice) -> Invoice:
    _DB[inv.invoice_id] = inv
    return inv


def mark_paid(invoice_id: str) -> Invoice:
    inv = _DB[invoice_id]
    if inv.status not in ("sent", "overdue"):
        raise ValueError(f"cannot pay an invoice in status {{inv.status}}")
    inv.status = "paid"
    return inv


def list_by_customer(customer_id: str) -> list[Invoice]:
    return [i for i in _DB.values() if i.customer_id == customer_id]
'''


def _test_module() -> str:
    return '''from services.invoicing.app import Invoice, create, mark_paid


def test_total_and_payment():
    inv = create(Invoice("INV-1", "C-1", "USD", "2026-12-01", status="sent", line_items=[("x", 500), ("y", 250)]))
    assert inv.amount_cents == 750
    assert mark_paid("INV-1").status == "paid"
'''
