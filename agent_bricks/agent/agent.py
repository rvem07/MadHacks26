"""The Lumen build agent, hosted with Databricks Agent Bricks (OpenAI Agents SDK).

This agent is deliberately thin. Everything that makes it observable and
safe lives in the Agent Flow Debugger app, not here:

  * Instructions are fetched from the app on every invocation
    (GET {AFD_APP_URL}/api/config/instructions), rendered from the versioned
    agent config. A teammate's rule edit takes effect on the next run with no
    redeploy.
  * Tools are fetched from the app's catalogue (GET /api/agent-tools) and every
    call is POSTed back to the app (POST /api/agent-tools/{tool}), where the
    single tool wrapper checks the stage allowlist, runs the tool against the
    run's pinned ontology version, and emits the semantic event the UI shows.
    The agent cannot reach GitHub, Gmail or the ontology any other way.
  * ask_human blocks inside that POST until a teammate replies in the app (or
    the stage timeout fires), so the agent "pauses" naturally.

The run_id is bound into every tool call here, so the model never has to
remember or forge it.

Environment
  AFD_APP_URL     base URL of the Agent Flow Debugger app (default http://127.0.0.1:8765)
  AFD_AGENT_MODEL Databricks serving endpoint for the agent (default databricks-gpt-oss-120b)
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any

import httpx
from agents import (Agent, FunctionTool, ModelSettings, Runner, set_default_openai_api, set_default_openai_client,
                    set_tracing_disabled)
from databricks_openai import AsyncDatabricksOpenAI

from databricks_agentkit import workspace_client, workspace_headers

# The OpenAI Agents SDK exports traces to OpenAI's servers by default. Turn
# that off: run data must stay in Databricks (MLflow tracing + our own events).
set_tracing_disabled(True)

MODEL = os.getenv("AFD_AGENT_MODEL", "databricks-gpt-oss-120b")
APP_URL = os.getenv("AFD_APP_URL", "http://127.0.0.1:8765").rstrip("/")
# ask_human can legitimately block for minutes while a teammate answers.
TOOL_TIMEOUT_S = float(os.getenv("AFD_TOOL_TIMEOUT_S", "900"))
MAX_TURNS = int(os.getenv("AFD_MAX_TURNS", "60"))


@lru_cache(maxsize=1)
def _configure_client() -> None:
    # Building the WorkspaceClient blocks on auth; configure the client once, not on every request.
    headers = workspace_headers()
    workspace = workspace_client()
    client = AsyncDatabricksOpenAI(workspace_client=workspace)
    if headers:
        # The scaffold passed default_headers to the constructor, which
        # databricks-openai 0.15 no longer accepts (and with_options() rebuilds
        # through that same constructor). The OpenAI base client sends
        # _custom_headers on every request, so add them there.
        client._custom_headers.update(headers)
    set_default_openai_client(client)
    set_default_openai_api("chat_completions")


def _app_headers() -> dict[str, str]:
    """Auth for calls into the app.

    Locally (http://) none is needed. When both are deployed as Databricks
    Apps, the agent's identity needs an OAuth token and CAN_USE on the
    debugger app. UNVERIFIED: written to the documented pattern, not yet
    exercised against a deployed pair.
    """
    if APP_URL.startswith("http://"):
        return {}
    return dict(workspace_client().config.authenticate())


def _make_tool(spec: dict, run_id: str, http: httpx.AsyncClient) -> FunctionTool:
    fn = spec["function"]
    params = json.loads(json.dumps(fn["parameters"]))
    # run_id is bound here, not chosen by the model.
    params["properties"].pop("run_id", None)
    params["required"] = [r for r in params.get("required", []) if r != "run_id"]

    async def on_invoke(_ctx, args_json: str) -> str:
        args = json.loads(args_json or "{}")
        resp = await http.post(f"{APP_URL}/api/agent-tools/{fn['name']}", json={"run_id": run_id, **args},
                               headers=_app_headers(), timeout=TOOL_TIMEOUT_S)
        if resp.status_code >= 400:
            return json.dumps({"status": "error", "detail": resp.text[:500]})
        return resp.text

    return FunctionTool(name=fn["name"], description=fn["description"], params_json_schema=params,
                        on_invoke_tool=on_invoke, strict_json_schema=False, timeout_seconds=TOOL_TIMEOUT_S)


async def invoke(messages: list[dict[str, Any]], run_id: str | None = None) -> dict[str, Any]:
    """Run the agent for one task. `run_id` ties every tool call to the app's run."""
    _configure_client()
    if not run_id:
        return {"output": [{"role": "assistant", "content": (
            "This agent is driven by the Agent Flow Debugger app. Start a run there (host: agentbricks) "
            "so every tool call is checked and logged.")}]}
    async with httpx.AsyncClient() as http:
        headers = _app_headers()
        instructions = (await http.get(f"{APP_URL}/api/config/instructions", headers=headers, timeout=30)).json()
        catalogue = (await http.get(f"{APP_URL}/api/agent-tools", headers=headers, timeout=30)).json()
        tools = [_make_tool(spec, run_id, http) for spec in catalogue]
        agent = Agent(name="Lumen build agent", instructions=instructions["markdown"], model=MODEL, tools=tools,
                      # (Databricks chat endpoints reject the parallel_tool_calls field.)
                      model_settings=ModelSettings(temperature=0.0))
        result = await Runner.run(agent, messages, max_turns=MAX_TURNS)
    return {"output": [{"role": "assistant", "content": str(result.final_output)}],
            "config_version": instructions.get("version")}
