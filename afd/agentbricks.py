"""Agent Bricks host: the LLM-driven agent, observed through the same wrapper.

How a run works with AFD_AGENT_HOST=agentbricks (or host="agentbricks" per run):

  1. RunManager creates the run, pins ontology + rules versions, and registers
     the run's ToolWrapper so callbacks can find it.
  2. run_with_agentbricks() POSTs the task to the Agent Bricks agent
     (agent_bricks/, deployed with `agentbricks deploy` or run locally with
     `agentbricks dev`) together with the run_id.
  3. The agent fetches its instructions (rendered from the current rules) and
     its tool catalogue from this app, and calls every tool back through
     POST /api/agent-tools/{tool} -> call_tool_for_run() -> ToolWrapper.call().
     So allowlists, protected branches, blocked-attempt events, milestones,
     narration and flags are identical to the scripted host.
  4. ask_human blocks inside that callback until a teammate replies in the
     app, so the agent pauses while waiting.

Why not the Supervisor Agent API? It is now a legacy feature: this account
can no longer create new Supervisor Agents ("Please use Genie Agents
instead"), and Genie Agents are read-only data Q&A. The Agent Bricks CLI is
Databricks' current code-first way to build custom agents, so that is the
host. (Verified against the workspace on 2026-11-14.)
"""
from __future__ import annotations

import logging

import httpx

from .config import Settings

log = logging.getLogger(__name__)


def _agent_headers(url: str) -> dict:
    """Auth for calling a deployed agent (Databricks Apps need an OAuth token)."""
    if url.startswith("http://"):
        return {}
    from databricks.sdk import WorkspaceClient

    return dict(WorkspaceClient().config.authenticate())


KICKOFF = """Task from teammate {requester}: {task}

You are the Lumen build agent. Follow your instructions exactly:
1. Call begin_stage("triage"), find the task in the ontology and what it affects, record_decision.
2. Call begin_stage("investigate"), follow depends_on edges from the affected service. Read readable documents
   with read_file (paths are in node props). For a document that is not readable, find its owner and ask_human
   ONCE, then cite_evidence for the answer. Do not ask the same person the same thing twice.
3. Call begin_stage("act"), github_create_branch("agent/<task-node-id>"), write the code with edit_file under
   services/<service>/, then github_open_pr. Follow the guidance people gave you.
4. Call begin_stage("verify"), cite_evidence for each claim, then conclude(answer, claims).
Never end your turn without calling conclude."""

CONTINUE = """You stopped before calling conclude. Here is what you have done so far (from the run log):
{log}

Continue from where you left off, in the current stage or the next one, and finish with conclude."""

MAX_INVOCATIONS = 3  # kickoff + up to two continuations (no conclude, or a failed invocation)


def run_with_agentbricks(run: dict, ctx, wrapper, settings: Settings) -> None:
    """Invoke the Agent Bricks agent for this run and wait for it to finish.

    If the agent ends its turn without calling conclude, it is re-invoked once
    with the milestone log so far (the custom server keeps no session state).
    """
    from .runs import get_manager

    mgr = get_manager()
    mgr.register_wrapper(run["run_id"], wrapper)
    url = settings.agentbricks_url.rstrip("/") + "/invocations"
    prompt = KICKOFF.format(requester=run["requester"], task=run["task"])
    try:
        for attempt in range(MAX_INVOCATIONS):
            try:
                resp = httpx.post(url, json={"input": [{"role": "user", "content": prompt}], "run_id": run["run_id"]},
                                  headers=_agent_headers(url), timeout=settings.agentbricks_timeout_s)
                resp.raise_for_status()
                final = resp.json().get("output", [{}])[-1].get("content", "")
            except httpx.HTTPStatusError as e:
                # The agent failed mid-turn (e.g. model rate limit). Its tool calls so
                # far are already events; record the failure and continue from the log.
                ctx.emit(stage=ctx.stage or "-", actor="system", action_type="observe", tool=None,
                         result={"error": f"agent invocation {attempt + 1} failed: HTTP {e.response.status_code}"},
                         status="error", milestone=True)
                if attempt == MAX_INVOCATIONS - 1:
                    raise
                final = None
            if final is not None:
                # The agent's last message is its own claim (reported); the structured
                # answer, if any, is the conclude event it emitted through the wrapper.
                ctx.emit(stage=ctx.stage or "-", actor="agent", action_type="observe", tool=None,
                         result={"final_message": final, "concluded_via_tool": ctx.concluded,
                                 "invocation": attempt + 1},
                         status="ok", milestone=not ctx.concluded)
            if ctx.concluded or ctx.stop_reason:
                break
            log_lines = [f"#{e['seq']} [{e['stage']}] {e['plain_summary']}"
                         for e in mgr.events(run["run_id"]) if e["milestone"] and e["plain_summary"]]
            prompt = CONTINUE.format(log="\n".join(log_lines[-40:]))
        if not ctx.concluded and not ctx.stop_reason:
            ctx.stop_reason = "the agent ended its turn without calling conclude"
    except Exception as e:
        ctx.stop_reason = f"Agent Bricks agent unreachable or failed: {type(e).__name__}: {e}"
        log.warning(ctx.stop_reason)
        ctx.emit(stage=ctx.stage or "-", actor="system", action_type="observe", tool=None,
                 result={"error": ctx.stop_reason}, status="error", milestone=True)
    finally:
        mgr.unregister_wrapper(run["run_id"])


def call_tool_for_run(run_id: str, tool: str, body: dict) -> dict:
    """Entry point for the agent's tool callbacks (see main.agent_tool_call)."""
    from .runs import get_manager

    wrapper = get_manager().wrapper_for(run_id)
    if wrapper is None:
        raise KeyError(f"run {run_id} is not waiting on an Agent Bricks agent")
    args = {k: v for k, v in body.items() if k != "run_id"}
    return wrapper.call(tool, args)


def sync_instructions() -> None:
    """Rules changed. Nothing to push: the agent fetches its instructions from
    /api/config/instructions on every invocation, so the next run uses them."""
    log.info("rules changed; the Agent Bricks agent will pick them up on its next invocation")
