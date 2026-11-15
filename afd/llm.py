"""Thin client for Databricks Foundation Model APIs / model serving.

No external API key: authentication is whatever the Databricks SDK resolves
(the app's service principal inside a Databricks App, or the CLI profile on a
laptop). Calls go to `/serving-endpoints/{name}/invocations` with an
OpenAI-compatible chat payload, which every chat endpoint in the target
workspace accepts (verified endpoints include databricks-gpt-oss-120b,
databricks-meta-llama-3-1-8b-instruct, databricks-qwen3-next-80b-a3b-instruct).
"""
from __future__ import annotations

import concurrent.futures
import logging

log = logging.getLogger(__name__)

_client = None
_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="llm")


def _workspace():
    global _client
    if _client is None:
        from databricks.sdk import WorkspaceClient

        _client = WorkspaceClient()
    return _client


def _extract_text(resp: dict) -> str:
    """Pull the assistant text out of a chat response.

    Reasoning models (gpt-oss) may return `content` as a list of typed parts
    (reasoning + text); only the text parts are the answer.
    """
    msg = resp["choices"][0]["message"]
    content = msg.get("content")
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if p.get("type") == "text").strip()
    return (content or "").strip()


def chat(endpoint: str, messages: list[dict], max_tokens: int = 400, temperature: float = 0.0,
         timeout_s: float = 30.0, **extra) -> str:
    """Synchronous chat call with a hard timeout. Raises on failure/timeout."""
    body = {"messages": messages, "max_tokens": max_tokens, "temperature": temperature, **extra}

    def _do():
        return _workspace().api_client.do("POST", f"/serving-endpoints/{endpoint}/invocations", body=body)

    fut = _pool.submit(_do)
    return _extract_text(fut.result(timeout=timeout_s))
