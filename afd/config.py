"""Environment-driven settings.

Everything that differs between "laptop demo" and "deployed Databricks App"
lives here, so the rest of the code never reads os.environ directly.
Defaults are chosen so the app runs fully offline and deterministically:
scripted agent host, template narrator, rules-based reviewer, simulated
GitHub, Gmail in dry-run, Delta mirror off.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    """Parse a boolean env var ("1", "true", "yes" are truthy)."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    # Where the local JSON store, simulated repo and benchmark output live.
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("AFD_DATA_DIR", "data")))

    # Which host drives the agent: "scripted" (deterministic policy, default)
    # or "agentbricks" (the LLM agent in agent_bricks/, built with the Agent Bricks CLI).
    agent_host: str = field(default_factory=lambda: os.environ.get("AFD_AGENT_HOST", "scripted"))

    # Narrator: "template" (always available) or "llm" (small hosted model,
    # with grounding check and template fallback).
    narrator_mode: str = field(default_factory=lambda: os.environ.get("AFD_NARRATOR", "template"))
    narrator_endpoint: str = field(
        default_factory=lambda: os.environ.get("AFD_NARRATOR_ENDPOINT", "databricks-meta-llama-3-1-8b-instruct")
    )
    narrator_timeout_s: float = field(default_factory=lambda: float(os.environ.get("AFD_NARRATOR_TIMEOUT_S", "4")))

    # Reviewer (fix proposer): "rules" (deterministic) or "llm" (stronger hosted model).
    reviewer_mode: str = field(default_factory=lambda: os.environ.get("AFD_REVIEWER", "rules"))
    reviewer_endpoint: str = field(
        default_factory=lambda: os.environ.get("AFD_REVIEWER_ENDPOINT", "databricks-gpt-oss-120b")
    )

    # Agent Bricks host: where the agent in agent_bricks/ is served
    # (`agentbricks dev` locally, or the deployed agent-bricks-afd-agent app URL).
    agentbricks_url: str = field(default_factory=lambda: os.environ.get("AFD_AGENTBRICKS_URL", "http://127.0.0.1:8000"))
    agentbricks_timeout_s: float = field(default_factory=lambda: float(os.environ.get("AFD_AGENTBRICKS_TIMEOUT_S", "1200")))

    # Delta mirror (Unity Catalog). Off unless explicitly enabled.
    delta_enabled: bool = field(default_factory=lambda: _bool("AFD_DELTA", False))
    catalog: str = field(default_factory=lambda: os.environ.get("AFD_CATALOG", "workspace"))
    schema: str = field(default_factory=lambda: os.environ.get("AFD_SCHEMA", "agent_flow"))
    warehouse_id: str = field(default_factory=lambda: os.environ.get("AFD_WAREHOUSE_ID", ""))

    # Genie (optional read-only data tool + benchmark condition).
    genie_space_id: str = field(default_factory=lambda: os.environ.get("AFD_GENIE_SPACE_ID", ""))

    # GitHub: "sim" (local simulated repo, default) or "rest" (real API, unverified).
    github_mode: str = field(default_factory=lambda: os.environ.get("AFD_GITHUB_MODE", "sim"))
    github_repo: str = field(default_factory=lambda: os.environ.get("AFD_GITHUB_REPO", ""))
    github_token: str = field(default_factory=lambda: os.environ.get("GITHUB_TOKEN", ""))

    # Gmail: "dry_run" (record the email as an event, send nothing; default) or "send".
    gmail_mode: str = field(default_factory=lambda: os.environ.get("AFD_GMAIL_MODE", "dry_run"))

    # Human-in-the-loop: if set, simulated teammates answer questions
    # automatically after this many seconds (demo/benchmark convenience).
    # Negative means "never auto-reply; a person must type the answer".
    auto_reply_s: float = field(default_factory=lambda: float(os.environ.get("AFD_AUTO_REPLY_S", "-1")))

    # Fixed "today" for staleness math (tests/benchmarks); defaults to the real date.
    today_override: str = field(default_factory=lambda: os.environ.get("AFD_TODAY", ""))

    def today(self) -> date:
        """The date used to compute edge age (last_verified -> days old)."""
        return date.fromisoformat(self.today_override) if self.today_override else date.today()


_settings: Settings | None = None


def get_settings() -> Settings:
    """Process-wide settings singleton (re-created by reset_settings in tests)."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings(**overrides) -> Settings:
    """Rebuild settings from env, then apply keyword overrides (used by tests)."""
    global _settings
    _settings = Settings()
    for key, value in overrides.items():
        setattr(_settings, key, value)
    return _settings
