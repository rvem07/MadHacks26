"""Foreground HTTP contract for the Lumen build agent (Agent Bricks custom server).

POST /invocations
  {"input": [{"role": "user", "content": "..."}], "run_id": "r_1234abcd"}
-> {"output": [{"role": "assistant", "content": "..."}], "config_version": 3}

The Agent Flow Debugger's run manager is the only intended caller: it creates
the run (pinning ontology + rules versions), then invokes this endpoint with
the run_id so the agent's tool calls land in that run.
"""
import os
from pathlib import Path
from typing import Any

import uvicorn
from agent.agent import invoke
from dotenv import load_dotenv
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field

# override=False so injected DATABRICKS_* (from `agentbricks dev -p` or the deploy platform) win over a
# checked-in .env.
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=False)


class InvocationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: list[dict[str, Any]] = Field(default_factory=list)
    run_id: str | None = Field(default=None, description="Agent Flow Debugger run this invocation belongs to.")


app = FastAPI(title="Lumen build agent (Agent Bricks)")


@app.post("/invocations")
async def invocations(body: InvocationRequest) -> dict[str, Any]:
    return await invoke(body.input, run_id=body.run_id)


def main() -> None:
    uvicorn.run("runtime.main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
