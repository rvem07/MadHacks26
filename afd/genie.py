"""Genie as a read-only data tool (and a benchmark condition).

The agent may ask the Genie space natural-language data questions about the
company tables. The Genie conversation id is recorded on the event
(external_ids.genie_conversation_id) so the trace links to Genie's own record.

Requires AFD_GENIE_SPACE_ID. Genie Ontology (the preview product) is NOT used.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def ask_genie(space_id: str, question: str, timeout_s: float = 60) -> dict:
    from datetime import timedelta

    from databricks.sdk import WorkspaceClient

    w = WorkspaceClient()
    msg = w.genie.start_conversation_and_wait(space_id, question, timeout=timedelta(seconds=timeout_s))
    text, sql = [], None
    for att in msg.attachments or []:
        if getattr(att, "text", None) and att.text.content:
            text.append(att.text.content)
        if getattr(att, "query", None):
            sql = att.query.query
            if att.query.description:
                text.append(att.query.description)
    rows = None
    if sql:
        try:
            for att in msg.attachments or []:
                if getattr(att, "query", None):
                    res = w.genie.get_message_attachment_query_result(
                        space_id, msg.conversation_id, msg.id, att.attachment_id)
                    data = res.statement_response.result
                    rows = data.data_array[:20] if data and data.data_array else []
        except Exception:
            log.info("could not fetch Genie query result", exc_info=True)
    return {"conversation_id": msg.conversation_id, "message_id": msg.id, "text": " ".join(text),
            "sql": sql, "rows": rows}
