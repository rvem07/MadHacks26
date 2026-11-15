"""Agent Flow Debugger (afd).

A glass-box control room for one staged AI agent at a small startup:
watch the agent walk the company ontology live, review the run afterwards,
and fix the agent's rules and the ontology with AI-proposed, human-approved diffs.

Module map (see README.md for the full walkthrough):
    config      environment-driven settings
    store       local JSON store (working state) + Delta write-behind mirror
    audit       who-changed-what log shared by every editor
    ontology    versioned graph (nodes/edges with provenance) + graph helpers
    agentcfg    versioned per-stage agent configuration + instruction rendering
    seed        the fictional 7-person startup used by the demo
    events      the semantic event model and fidelity labels
    repo        simulated single GitHub repo (branch-per-task, PRs, protected main)
    channels    human-in-the-loop channels (in-app primary, Gmail mirror)
    tools       the single tool-wrapper layer every agent action passes through
    policy      the deterministic scripted agent policy (demo / test / benchmark host)
    agentbricks Agent Bricks host: invokes the agent in agent_bricks/ and routes its tool calls to the wrapper
    genie       Genie space as a read-only data tool
    llm         Databricks model-serving client (narrator, reviewer)
    narrator    milestone narration with grounding check + template fallback
    flags       automatic post-mortem flags over trace + ontology
    reviewer    AI-proposed fixes as before/after diffs (never auto-applied)
    runs        run manager: threads, pause/reply, timeouts, marking, rerun, compare
    benchmark   four-condition benchmark over seeded ontology failures
    main        FastAPI app (REST + server-sent events + static UI)
"""
