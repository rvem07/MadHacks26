"""Human-in-the-loop: one "ask a person" mechanism with swappable channels.

Spec section 8, with the user's change: Slack is dropped; Gmail is the only
external channel.

  * In-app (primary): the question is stored, appears in the app addressed to
    that person, and the agent's thread blocks until someone replies in the
    input box or the stage's timeout expires.
  * Gmail (mirror): the same question is also sent as an email to the person's
    address from the ontology. Default AFD_GMAIL_MODE=dry_run records exactly
    what would be sent and sends nothing. Real sending is UNVERIFIED and off
    by default; reading email replies back into a run is a stretch goal and
    is not implemented.

Flat access: any teammate may answer on someone's behalf; the reply records
who actually typed it (`answered_by`) for attribution.

For demos and the benchmark, AFD_AUTO_REPLY_S makes simulated teammates
answer after a delay, using the persona replies below.
"""
from __future__ import annotations

import logging
import threading
import uuid

from .audit import now_iso
from .store import LocalStore

log = logging.getLogger(__name__)

# Simulated teammates. Priya's security answer is the demo's key beat: it is
# plausible and specific, but it describes the OLD policy. It is not an
# obvious "that's not me", which would let the agent self-correct.
PERSONA_REPLIES = {
    "p_priya": {
        "security": ("For anything touching payments we sign service tokens with HS256 using the shared "
                     "secret in the vault, rotate it every 180 days, and log auth failures to the audit table. "
                     "That's what payments does today, so mirror it."),
        "retention": "Invoices are kept for 7 years, then archived to cold storage; PII is masked after 2 years.",
        "_default": "I think that's right, go ahead.",
    },
    "p_marcus": {
        "security": ("Use the v3 security spec: tokens are RS256-signed by the auth service (no shared secrets), "
                     "keys rotate every 90 days, and invoice PII fields are encrypted at rest. Tag me on the PR."),
        "_default": "Happy to review, tag me on the PR.",
    },
    "p_dana": {"_default": "Follow the invoice requirements doc; ping me if a field is unclear."},
    "p_leo": {"api": "Use REST, plural nouns, cursor pagination, and return errors as RFC 7807 problem JSON.",
              "_default": "Works for me, open a PR and I'll review."},
    "p_sam": {"_default": "Use the design tokens in web/theme; no inline colors."},
    "p_ana": {"_default": "Finance needs the invoice totals in cents and an export to CSV."},
    "p_kai": {"retention": "Retention is 5 years for everything (old policy page).", "_default": "Sounds fine."},
}


def persona_reply(person_id: str, question: str) -> str:
    """Pick a simulated teammate's reply by topic keyword."""
    replies = PERSONA_REPLIES.get(person_id, {"_default": "I can help with that."})
    q = question.lower()
    for topic, text in replies.items():
        if topic != "_default" and topic in q:
            return text
    return replies["_default"]


class HumanChannel:
    """In-app question queue with blocking waits, plus the Gmail mirror."""

    def __init__(self, store: LocalStore, gmail_mode: str = "dry_run", auto_reply_s: float = -1.0):
        self.store = store
        self.gmail_mode = gmail_mode
        self.auto_reply_s = auto_reply_s
        self._waiters: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def ask(self, *, run_id: str, seq: int, person: dict, question: str, asked_by: str = "agent",
            mirror_email: bool = True) -> dict:
        """Create an open question (non-blocking). Call wait() to block on it."""
        qid = "q_" + uuid.uuid4().hex[:8]
        email = self._mirror_email(qid, person, question) if mirror_email else None
        row = {
            "question_id": qid, "run_id": run_id, "seq": seq, "to_person": person["id"],
            "to_label": person["label"], "text": question, "status": "open", "asked_by": asked_by,
            "created_at": now_iso(), "updated_at": now_iso(), "reply": None, "answered_by": None,
            "channels": ["in_app"] + (["gmail"] if email else []), "email": email,
        }
        with self._lock:
            self._waiters[qid] = threading.Event()
        self.store.insert("questions", row)
        if self.auto_reply_s >= 0:
            timer = threading.Timer(self.auto_reply_s, self._auto_reply, args=(qid, person["id"], question))
            timer.daemon = True
            timer.start()
        return row

    def wait(self, question_id: str, timeout_s: float) -> dict:
        """Block until replied or timeout; returns the final question row."""
        ev = self._waiters.get(question_id)
        if ev and not ev.wait(timeout_s):
            self.store.update("questions", "question_id", question_id, {"status": "timeout", "updated_at": now_iso()})
        return self.store.get("questions", question_id=question_id)

    def reply(self, question_id: str, text: str, answered_by: str) -> dict:
        """Record a reply (from the in-app box or an auto-reply) and wake the agent."""
        q = self.store.get("questions", question_id=question_id)
        if not q:
            raise KeyError(question_id)
        if q["status"] != "open":
            raise ValueError(f"question {question_id} is {q['status']}")
        row = self.store.update("questions", "question_id", question_id, {
            "status": "answered", "reply": text, "answered_by": answered_by, "updated_at": now_iso()})
        ev = self._waiters.get(question_id)
        if ev:
            ev.set()
        return row

    def cancel(self, question_id: str) -> None:
        """Close a question whose run ended (e.g. stopped); wakes any waiter."""
        self.store.update("questions", "question_id", question_id, {"status": "cancelled", "updated_at": now_iso()})
        ev = self._waiters.get(question_id)
        if ev:
            ev.set()

    def open_questions(self) -> list[dict]:
        return [q for q in self.store.all("questions") if q["status"] == "open"]

    # -- internals -------------------------------------------------------------
    def _auto_reply(self, qid: str, person_id: str, question: str) -> None:
        try:
            self.reply(qid, persona_reply(person_id, question), answered_by=f"simulated:{person_id}")
        except (KeyError, ValueError):
            pass  # already answered by a person, timed out, or cancelled

    def _mirror_email(self, qid: str, person: dict, question: str) -> dict | None:
        """Gmail mirror. Dry-run records the would-be email; nothing is sent."""
        to = person.get("props", {}).get("email")
        if not to:
            return None
        email = {"to": to, "subject": f"[Lumen agent] Question for {person['label']}",
                 "body": f"{question}\n\nReply in the Agent Flow Debugger app (question {qid}).",
                 "mode": self.gmail_mode, "message_id": f"dryrun-{qid}"}
        if self.gmail_mode == "send":  # pragma: no cover - UNVERIFIED, needs Gmail API credentials
            log.warning("AFD_GMAIL_MODE=send is not wired to a Gmail connection in this build; recording only")
            email["mode"] = "dry_run (send not configured)"
        return email


def send_message_email(person: dict, text: str, gmail_mode: str) -> dict | None:
    """One-way message mirror used by the message_human tool (same dry-run rules)."""
    to = person.get("props", {}).get("email")
    if not to:
        return None
    return {"to": to, "subject": "[Lumen agent] Update", "body": text, "mode": gmail_mode,
            "message_id": "dryrun-" + uuid.uuid4().hex[:8]}
