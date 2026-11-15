"""Seed data: "Lumen Ledger", a fictional seven-person invoicing startup.

The seed is built around the decided demo (spec section 13, "stale owner"):

  * Priya Shah used to own security. The edge `Priya owns Security specs`
    was last verified 2025-08-01 (well over a year before the demo date)
    and came from an old onboarding doc, so confidence is low.
  * Priya has since moved to the Data team (fresh HR edge).
  * Marcus Reed now leads security. Two *fresh* second sources say so:
    CODEOWNERS (`Marcus responsible_for security/`), and the security spec
    lives in that repo area (`Security specs depends_on security/`).
  * The task "Build the invoicing backend" affects the Invoicing service,
    which depends on the Security specs.

So an agent that trusts the stale edge asks Priya; an agent with the
"confirm stale owners with a second source" rule asks Marcus.

All people use the reserved `.example` domain so no real address is ever used.
The seed also contains the extra documents/services the benchmark uses for
its seeded failures (missing edge, conflicting sources, loop, wrong mapping).
"""
from __future__ import annotations

from .agentcfg import ConfigService
from .ontology import OntologyService
from .store import LocalStore

SEED_AUTHOR = "seed"

# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------
PEOPLE = [
    # id, label, title, email
    ("p_priya", "Priya Shah", "Data engineer (former security lead)", "priya@lumenledger.example"),
    ("p_marcus", "Marcus Reed", "Security lead", "marcus@lumenledger.example"),
    ("p_dana", "Dana Okafor", "CEO / product", "dana@lumenledger.example"),
    ("p_leo", "Leo Martins", "Backend engineer", "leo@lumenledger.example"),
    ("p_sam", "Sam Chen", "Frontend engineer", "sam@lumenledger.example"),
    ("p_ana", "Ana Ruiz", "Ops & finance", "ana@lumenledger.example"),
    ("p_kai", "Kai Novak", "Data analyst", "kai@lumenledger.example"),
]

TEAMS = [
    ("team_eng", "Engineering"), ("team_security", "Security"),
    ("team_data", "Data"), ("team_product", "Product & Ops"),
]

SERVICES = [
    ("svc_invoicing", "Invoicing service", {"status": "planned", "aliases": ["invoice", "invoicing backend", "billing"]}),
    ("svc_payments", "Payments service", {"status": "live"}),
    ("svc_auth", "Auth service", {"status": "live", "aliases": ["authentication", "tokens"]}),
    ("svc_notifications", "Notifications service", {"status": "live"}),
    ("svc_scheduler", "Scheduler service", {"status": "live"}),
    ("svc_warehouse", "Data warehouse", {"status": "live"}),
]

REPO_AREAS = [
    ("ra_backend", "services/ (backend)", {"path": "services/"}),
    ("ra_security", "security/", {"path": "security/"}),
    ("ra_frontend", "web/ (frontend)", {"path": "web/"}),
    ("ra_data", "pipelines/ (data)", {"path": "pipelines/"}),
]

DOCUMENTS = [
    # readable=False means "lives in an internal wiki the agent cannot read":
    # the agent has to ask a person for its contents.
    ("doc_security_specs", "Security specs", {"readable": False, "aliases": ["security specification", "security policy"]}),
    ("doc_invoice_requirements", "Invoice requirements", {"readable": True, "path": "docs/invoice_requirements.md"}),
    ("doc_api_guidelines", "API guidelines", {"readable": False}),
    ("doc_data_retention", "Data retention policy", {"readable": False}),
    ("doc_oncall_runbook", "On-call runbook", {"readable": False}),
    ("doc_frontend_style", "Frontend style guide", {"readable": False}),
]

TICKETS = [
    ("tkt_101", "TKT-101 Invoice PDF export", {"status": "open"}),
    ("tkt_102", "TKT-102 Rotate API signing keys", {"status": "in progress"}),
    ("tkt_103", "TKT-103 Late payment reminders", {"status": "open"}),
]

TASKS = [
    ("task_invoicing_backend", "Build the invoicing backend",
     {"aliases": ["invoicing backend", "invoice backend", "build backend invoicing feature"]}),
    ("task_payment_retry", "Add payment retry logic", {}),
]


def seed_nodes() -> list[dict]:
    nodes = []
    for pid, label, title, email in PEOPLE:
        nodes.append({"id": pid, "type": "person", "label": label, "props": {"title": title, "email": email}})
    for tid, label in TEAMS:
        nodes.append({"id": tid, "type": "team", "label": label, "props": {}})
    for kind, rows in (("service", SERVICES), ("repo_area", REPO_AREAS), ("document", DOCUMENTS),
                       ("ticket", TICKETS), ("task", TASKS)):
        for nid, label, props in rows:
            nodes.append({"id": nid, "type": kind, "label": label, "props": props})
    return nodes


# ---------------------------------------------------------------------------
# Edges: (id, src, type, dst, source, confidence, last_verified)
# ---------------------------------------------------------------------------
EDGES = [
    # --- the demo's stale edge ------------------------------------------
    ("e_priya_owns_secspec", "p_priya", "owns", "doc_security_specs", "onboarding doc (2025)", 0.6, "2025-08-01"),
    # --- where Priya and Marcus actually are now ---------------------------
    ("e_priya_member_data", "p_priya", "member_of", "team_data", "HR directory", 0.95, "2026-10-20"),
    ("e_marcus_member_sec", "p_marcus", "member_of", "team_security", "HR directory", 0.95, "2026-10-20"),
    ("e_marcus_resp_security", "p_marcus", "responsible_for", "ra_security", "CODEOWNERS", 0.9, "2026-10-28"),
    ("e_secspec_dep_security", "doc_security_specs", "depends_on", "ra_security", "repo scan", 0.9, "2026-10-28"),
    ("e_secteam_owns_security", "team_security", "owns", "ra_security", "CODEOWNERS", 0.9, "2026-10-28"),
    ("e_marcus_owns_auth", "p_marcus", "owns", "svc_auth", "service catalog", 0.9, "2026-10-01"),
    # --- the task and the service it touches -------------------------------
    ("e_task_affects_invoicing", "task_invoicing_backend", "affects", "svc_invoicing", "ticket tracker", 0.95, "2026-11-10"),
    ("e_leo_works_task", "p_leo", "works_on", "task_invoicing_backend", "ticket tracker", 0.9, "2026-11-10"),
    ("e_invoicing_dep_payments", "svc_invoicing", "depends_on", "svc_payments", "architecture doc", 0.85, "2026-09-15"),
    ("e_invoicing_dep_auth", "svc_invoicing", "depends_on", "svc_auth", "architecture doc", 0.85, "2026-09-15"),
    ("e_invoicing_dep_secspec", "svc_invoicing", "depends_on", "doc_security_specs", "architecture doc", 0.8, "2026-09-15"),
    ("e_invoicing_dep_invreq", "svc_invoicing", "depends_on", "doc_invoice_requirements", "architecture doc", 0.9, "2026-11-01"),
    ("e_backend_affects_invoicing", "ra_backend", "affects", "svc_invoicing", "repo scan", 0.9, "2026-10-28"),
    ("e_leo_resp_backend", "p_leo", "responsible_for", "ra_backend", "CODEOWNERS", 0.9, "2026-10-28"),
    ("e_leo_owns_payments", "p_leo", "owns", "svc_payments", "service catalog", 0.85, "2026-07-01"),
    ("e_dana_owns_invreq", "p_dana", "owns", "doc_invoice_requirements", "doc metadata", 0.9, "2026-11-01"),
    # --- everyone else ------------------------------------------------------
    ("e_leo_member_eng", "p_leo", "member_of", "team_eng", "HR directory", 0.95, "2026-10-20"),
    ("e_sam_member_eng", "p_sam", "member_of", "team_eng", "HR directory", 0.95, "2026-10-20"),
    ("e_dana_member_prod", "p_dana", "member_of", "team_product", "HR directory", 0.95, "2026-10-20"),
    ("e_ana_member_prod", "p_ana", "member_of", "team_product", "HR directory", 0.95, "2026-10-20"),
    ("e_kai_member_data", "p_kai", "member_of", "team_data", "HR directory", 0.95, "2026-10-20"),
    ("e_sam_resp_frontend", "p_sam", "responsible_for", "ra_frontend", "CODEOWNERS", 0.9, "2026-10-28"),
    ("e_kai_resp_data", "p_kai", "responsible_for", "ra_data", "CODEOWNERS", 0.9, "2026-10-28"),
    ("e_datateam_owns_wh", "team_data", "owns", "svc_warehouse", "service catalog", 0.85, "2026-09-01"),
    ("e_tkt101_affects_inv", "tkt_101", "affects", "svc_invoicing", "ticket tracker", 0.9, "2026-11-05"),
    ("e_tkt102_affects_auth", "tkt_102", "affects", "svc_auth", "ticket tracker", 0.9, "2026-11-05"),
    ("e_marcus_works_tkt102", "p_marcus", "works_on", "tkt_102", "ticket tracker", 0.9, "2026-11-05"),
    ("e_ana_works_tkt103", "p_ana", "works_on", "tkt_103", "ticket tracker", 0.9, "2026-11-05"),
    ("e_tkt103_affects_notif", "tkt_103", "affects", "svc_notifications", "ticket tracker", 0.85, "2026-11-05"),
    ("e_payretry_affects_pay", "task_payment_retry", "affects", "svc_payments", "ticket tracker", 0.9, "2026-11-08"),
    # --- benchmark fixtures (seeded failures; see afd/benchmark.py) ---------
    # missing edge: nothing "owns" the API guidelines; only CODEOWNERS knows.
    ("e_apiguide_dep_backend", "doc_api_guidelines", "depends_on", "ra_backend", "repo scan", 0.9, "2026-10-28"),
    # conflicting sources: an old, confident edge vs a fresh, less confident one.
    ("e_kai_owns_retention", "p_kai", "owns", "doc_data_retention", "old wiki page", 0.9, "2025-03-01"),
    ("e_priya_owns_retention", "p_priya", "owns", "doc_data_retention", "data team charter", 0.7, "2026-10-01"),
    # loop: the runbook depends on a pair of services that depend on each other.
    ("e_runbook_dep_notif", "doc_oncall_runbook", "depends_on", "svc_notifications", "runbook header", 0.8, "2026-08-01"),
    ("e_notif_dep_sched", "svc_notifications", "depends_on", "svc_scheduler", "architecture doc", 0.8, "2026-08-01"),
    ("e_sched_dep_notif", "svc_scheduler", "depends_on", "svc_notifications", "architecture doc", 0.8, "2026-08-01"),
    # wrong mapping: the frontend style guide is mapped to the *backend* area.
    ("e_style_dep_backend", "doc_frontend_style", "depends_on", "ra_backend", "repo scan (misread)", 0.75, "2026-10-28"),
]


def seed_edges() -> list[dict]:
    return [{"id": eid, "src": s, "type": t, "dst": d, "source": src, "confidence": c,
             "last_verified": lv, "props": {}} for eid, s, t, d, src, c, lv in EDGES]


# ---------------------------------------------------------------------------
# Agent config v1
# ---------------------------------------------------------------------------
ALL_NODE_TYPES = ["person", "team", "service", "repo_area", "ticket", "task", "document"]
ALL_EDGE_TYPES = ["owns", "member_of", "depends_on", "affects", "works_on", "responsible_for"]


def seed_config() -> dict:
    """Config v1. Deliberately has NO stale-owner rule: the demo's first run
    must go wrong so the post-mortem can propose (and a human accept) it."""
    return {
        "agent_name": "Lumen build agent",
        "description": ("Takes a task from any teammate, walks the Lumen Ledger ontology to find the "
                        "information and people it needs, asks them, then does the work on its own "
                        "branch and opens a pull request."),
        "repo": {"name": "lumen/ledger", "protected_branches": ["main"], "branch_prefix": "agent/"},
        "stages": [
            {
                "name": "triage",
                "goal": "Understand the task and locate it in the ontology: which task node it is and what it affects.",
                "allowed_node_types": ["task", "ticket", "service", "team", "document", "repo_area"],
                "allowed_edge_types": ["affects", "works_on"],
                "allowed_tools": ["begin_stage", "ontology_lookup", "ontology_traverse", "record_decision"],
                "rules": [],
                "escalation": {"ask_timeout_s": 120, "on_timeout": "stop", "max_steps": 10,
                               "stop_when": "the task cannot be matched to any task or ticket node"},
                "apps": {},
            },
            {
                "name": "investigate",
                "goal": ("Find the documents, services and people the work depends on. When information "
                         "is not readable, ask the person who owns it and wait for the answer."),
                "allowed_node_types": ALL_NODE_TYPES,
                "allowed_edge_types": ALL_EDGE_TYPES,
                "allowed_tools": ["begin_stage", "ontology_lookup", "ontology_traverse", "record_decision",
                                  "cite_evidence", "ask_human", "read_file", "analyze_csv", "web_search",
                                  "genie_query"],
                "rules": [
                    {"id": "r_ask_owner", "text": "When a required document is not readable, ask its owner rather than guessing.",
                     "enabled": True, "check": None},
                ],
                "escalation": {"ask_timeout_s": 120, "on_timeout": "escalate_to_second_source", "max_steps": 30,
                               "stop_when": "no owner can be found for a required document"},
                "apps": {"github": "read", "gmail": "read_write", "web": "read", "genie": "read"},
            },
            {
                "name": "act",
                "goal": "Do the work on a branch named agent/<task-id> and open a pull request. Never push to main.",
                "allowed_node_types": ["service", "repo_area", "document"],
                "allowed_edge_types": ["affects", "depends_on"],
                "allowed_tools": ["begin_stage", "read_file", "edit_file", "analyze_csv", "github_create_branch",
                                  "github_open_pr", "message_human", "record_decision"],
                "rules": [
                    {"id": "r_branch_only", "text": "Only write to your own agent/ branch; open a pull request for review.",
                     "enabled": True, "check": None},
                ],
                "escalation": {"ask_timeout_s": 120, "on_timeout": "stop", "max_steps": 20, "stop_when": ""},
                "apps": {"github": "read_write", "gmail": "read_write"},
            },
            {
                "name": "verify",
                "goal": "Check the work against what was learned, state the result, and cite evidence for every claim.",
                "allowed_node_types": ALL_NODE_TYPES,
                "allowed_edge_types": ALL_EDGE_TYPES,
                "allowed_tools": ["begin_stage", "ontology_lookup", "read_file", "cite_evidence", "record_decision", "conclude"],
                "rules": [
                    {"id": "r_require_evidence", "text": "Every claim in the conclusion must cite evidence.",
                     "enabled": True, "check": {"type": "require_evidence"}},
                ],
                "escalation": {"ask_timeout_s": 120, "on_timeout": "stop", "max_steps": 20, "stop_when": ""},
                "apps": {"github": "read"},
            },
        ],
    }


# ---------------------------------------------------------------------------
# Simulated repo contents (main branch) - see afd/repo.py
# ---------------------------------------------------------------------------
REPO_FILES = {
    "README.md": "# lumen/ledger\n\nMonorepo for Lumen Ledger: invoicing for small agencies.\n",
    "CODEOWNERS": ("# Who reviews what (kept current by the eng lead)\n"
                   "services/   @leo\nsecurity/   @marcus\nweb/        @sam\npipelines/  @kai\n"),
    "docs/invoice_requirements.md": (
        "# Invoice requirements (owner: Dana)\n\n"
        "- An invoice has: id, customer_id, line items, currency, due_date, status.\n"
        "- Status flow: draft -> sent -> paid | overdue | void.\n"
        "- Totals are computed server-side in minor units (cents).\n"
        "- Endpoints: create, get, list by customer, send, mark paid.\n"
    ),
    "docs/team_wiki.md": (
        "# Team wiki: who to ask (last edited 2025-06)\n\n"
        "- Security specs - Priya\n- API guidelines - Leo\n- Data retention policy - Kai\n"
        "- Invoice requirements - Dana\n- Frontend style guide - Sam\n- Auth service - Marcus\n"
    ),
    "finance/invoices_sample.csv": (
        "invoice_id,customer_id,amount_cents,currency,status,due_date\n"
        "INV-1001,C-17,125000,USD,paid,2026-09-30\n"
        "INV-1002,C-17,98000,USD,overdue,2026-10-15\n"
        "INV-1003,C-22,450000,EUR,sent,2026-11-30\n"
        "INV-1004,C-31,12000,USD,draft,2026-12-05\n"
        "INV-1005,C-22,450000,EUR,paid,2026-10-31\n"
    ),
    "services/payments/app.py": "# Payments service (owner: Leo)\n",
    "security/README.md": "# security/ (owner: @marcus)\n\nThe current security spec lives in the internal wiki.\n",
}


def seed_all(store: LocalStore, force: bool = False) -> bool:
    """Write ontology v1, config v1 and the repo if the store is empty.

    Returns True if anything was seeded. `force` is only for tests.
    """
    from .repo import Repo

    onto, cfg = OntologyService(store), ConfigService(store)
    did = False
    if force or onto.current_version() == 0:
        onto.commit(seed_nodes(), seed_edges(), SEED_AUTHOR, "seed: Lumen Ledger ontology", action="seed")
        did = True
    if force or cfg.current_version() == 0:
        cfg.commit(seed_config(), SEED_AUTHOR, "seed: default staged agent", action="seed")
        did = True
    repo = Repo(store)
    if force or not repo.branches():
        repo.reset(REPO_FILES)
        did = True
    return did
