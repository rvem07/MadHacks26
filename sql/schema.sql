-- Agent Flow Debugger: Unity Catalog schema.
-- ${fq} is replaced with `catalog`.`schema` by scripts/setup_delta.py.
-- All tables are append-only: mutable records (runs, questions, proposals)
-- are re-appended with a newer updated_at; read the latest row per id
-- (the *_latest views do this). Comments are deliberate: Genie reads them.

CREATE SCHEMA IF NOT EXISTS ${fq}
COMMENT 'Agent Flow Debugger: versioned company ontology, agent rules, and the semantic event trace of every agent run.';

-- ---------------------------------------------------------------- ontology
CREATE TABLE IF NOT EXISTS ${fq}.ontology_versions (
  version     INT       COMMENT 'Ontology version number. Every edit or rollback writes a new version.',
  created_at  TIMESTAMP COMMENT 'When this version was written (UTC).',
  author      STRING    COMMENT 'Teammate who made the change (attribution, not permission).',
  note        STRING    COMMENT 'What changed and why, e.g. "accepted proposal pr_1234".'
) COMMENT 'One row per ontology version. Runs record the version they ran against so they can be replayed.';

CREATE TABLE IF NOT EXISTS ${fq}.nodes (
  ontology_version INT    COMMENT 'Ontology version this snapshot row belongs to.',
  node_id          STRING COMMENT 'Stable node id, e.g. p_priya, svc_invoicing, doc_security_specs.',
  node_type        STRING COMMENT 'One of: person, team, service, repo_area, ticket, task, document.',
  label            STRING COMMENT 'Human-readable name, e.g. "Priya Shah" or "Security specs".',
  props            STRING COMMENT 'JSON properties: person title/email, repo_area path, document readability.'
) COMMENT 'Ontology nodes: the people, teams, services, repo areas, tickets, tasks and documents of the startup. Full snapshot per ontology_version.';

CREATE TABLE IF NOT EXISTS ${fq}.edges (
  ontology_version INT    COMMENT 'Ontology version this snapshot row belongs to.',
  edge_id          STRING COMMENT 'Stable edge id.',
  src              STRING COMMENT 'Source node_id (e.g. the person who owns something).',
  dst              STRING COMMENT 'Destination node_id (e.g. the document that is owned).',
  edge_type        STRING COMMENT 'One of: owns, member_of, depends_on, affects, works_on, responsible_for. "owns" and "responsible_for" mean accountability.',
  source           STRING COMMENT 'Provenance: where this fact came from (HR directory, CODEOWNERS, an old onboarding doc...).',
  confidence       DOUBLE COMMENT 'Provenance: confidence in this fact, 0 to 1.',
  last_verified    DATE   COMMENT 'Provenance: when this fact was last confirmed. Older than 180 days is considered stale.',
  props            STRING COMMENT 'JSON extra properties.'
) COMMENT 'Ontology edges with provenance (source, confidence, last_verified). Full snapshot per ontology_version.';

-- ---------------------------------------------------------------- agent rules
CREATE TABLE IF NOT EXISTS ${fq}.agent_config_versions (
  version    INT       COMMENT 'Agent rules version. Every edit or rollback writes a new version.',
  created_at TIMESTAMP COMMENT 'When this version was written (UTC).',
  author     STRING    COMMENT 'Teammate who made the change.',
  note       STRING    COMMENT 'What changed and why.',
  config     STRING    COMMENT 'JSON: per-stage goal, allowed tools/node types/edge types, plain-language rules, escalation, app allowlist.'
) COMMENT 'The agent defined as data, versioned. Runs record the version they used.';

-- ---------------------------------------------------------------- trace
CREATE TABLE IF NOT EXISTS ${fq}.events (
  run_id           STRING    COMMENT 'Agent run this event belongs to.',
  seq              INT       COMMENT 'Order of the event within the run (1, 2, 3...).',
  event_id         STRING    COMMENT 'run_id:seq. Used by the agent to cite evidence.',
  ts               TIMESTAMP COMMENT 'When the event happened (UTC).',
  stage            STRING    COMMENT 'Agent stage: triage, investigate, act or verify.',
  actor            STRING    COMMENT '"agent", "system", or "human:<person_id>" for a human reply.',
  action_type      STRING    COMMENT 'observe, traverse, query, decide, ask_human, act, conclude or blocked.',
  tool             STRING    COMMENT 'Tool that produced the event (ontology_traverse, ask_human, github_open_pr...).',
  node_ids         STRING    COMMENT 'JSON array of ontology node ids involved.',
  edge_ids         STRING    COMMENT 'JSON array of ontology edge ids involved.',
  input            STRING    COMMENT 'JSON tool input (observed).',
  result           STRING    COMMENT 'JSON tool result (observed).',
  status           STRING    COMMENT 'ok, blocked, error, waiting or timeout.',
  latency_ms       INT       COMMENT 'Tool latency in milliseconds.',
  decision         STRING    COMMENT 'JSON {options, chosen, rationale}: the agent''s own stated reasoning (reported).',
  evidence_refs    STRING    COMMENT 'JSON array of event ids or node/edge ids supporting a claim.',
  milestone        BOOLEAN   COMMENT 'True if the tool wrapper marked this a milestone (narrated in plain language).',
  plain_summary    STRING    COMMENT 'Narrator sentence (inferred). Empty for non-milestones.',
  narrator         STRING    COMMENT '"llm" or "template": which narrator produced plain_summary.',
  config_version   INT       COMMENT 'Agent rules version the run used.',
  ontology_version INT       COMMENT 'Ontology version the run used.',
  external_ids     STRING    COMMENT 'JSON ids in other systems: github_pr, gmail_message_id, genie_conversation_id.',
  warnings         STRING    COMMENT 'JSON array of rule warnings, e.g. stale owner edges.',
  fidelity         STRING    COMMENT 'JSON map of which parts are observed / reported / inferred.'
) COMMENT 'One semantic event per agent tool call or decision. The glass-box trace.';

-- Mutable records: append-only with updated_at; read the latest row per id.
CREATE TABLE IF NOT EXISTS ${fq}.runs (
  id STRING COMMENT 'run_id', run_id STRING COMMENT 'run_id',
  updated_at TIMESTAMP COMMENT 'When this state of the run was recorded.',
  record STRING COMMENT 'JSON run record: task, requester, status, outcome, answer, claims, versions, marked_wrong, expected_outcome.'
) COMMENT 'Agent runs (latest row per id is current).';

CREATE TABLE IF NOT EXISTS ${fq}.questions (
  id STRING COMMENT 'question_id', run_id STRING COMMENT 'Run that asked.',
  updated_at TIMESTAMP COMMENT 'When this state was recorded.',
  record STRING COMMENT 'JSON: to_person, text, status (open/answered/timeout), reply, answered_by, Gmail mirror.'
) COMMENT 'Questions the agent asked teammates (human in the loop).';

CREATE TABLE IF NOT EXISTS ${fq}.flags (
  id STRING COMMENT 'flag_id', run_id STRING COMMENT 'Flagged run.',
  updated_at TIMESTAMP COMMENT 'When the flag was raised.',
  record STRING COMMENT 'JSON: type (stale_edge_used, loop, dead_end, unsupported_claim, blocked_action...), seq, detail.'
) COMMENT 'Automatic post-mortem flags (inferred).';

CREATE TABLE IF NOT EXISTS ${fq}.proposals (
  id STRING COMMENT 'proposal_id', run_id STRING COMMENT 'Run the proposal came from.',
  updated_at TIMESTAMP COMMENT 'When this state was recorded.',
  record STRING COMMENT 'JSON: target (ontology/config), ops, before, after, status (pending/accepted/rejected), proposer.'
) COMMENT 'AI-proposed fixes. Never applied automatically.';

CREATE TABLE IF NOT EXISTS ${fq}.test_cases (
  id STRING COMMENT 'test_case_id', run_id STRING COMMENT 'Run a teammate marked wrong.',
  updated_at TIMESTAMP COMMENT 'When it was saved.',
  record STRING COMMENT 'JSON: task, expected_outcome, divergence step.'
) COMMENT 'Runs marked wrong, saved as regression cases.';

CREATE TABLE IF NOT EXISTS ${fq}.audit_log (
  id STRING COMMENT 'audit_id', run_id STRING COMMENT 'Unused (empty).',
  updated_at TIMESTAMP COMMENT 'When the change happened.',
  record STRING COMMENT 'JSON: actor, action, target, before, after, note.'
) COMMENT 'Who changed what, when. Attribution instead of permissions.';

-- ---------------------------------------------------------------- views
CREATE OR REPLACE VIEW ${fq}.nodes_current
COMMENT 'Ontology nodes in the latest ontology version.'
AS SELECT * FROM ${fq}.nodes WHERE ontology_version = (SELECT max(version) FROM ${fq}.ontology_versions);

CREATE OR REPLACE VIEW ${fq}.edges_current
COMMENT 'Ontology edges in the latest version, with node labels and age in days. Use this to answer "who owns X".'
AS SELECT e.edge_id, e.src, s.label AS src_label, s.node_type AS src_type, e.edge_type, e.dst, d.label AS dst_label,
          d.node_type AS dst_type, e.source, e.confidence, e.last_verified,
          datediff(current_date(), e.last_verified) AS age_days
   FROM ${fq}.edges e
   JOIN ${fq}.nodes s ON s.node_id = e.src AND s.ontology_version = e.ontology_version
   JOIN ${fq}.nodes d ON d.node_id = e.dst AND d.ontology_version = e.ontology_version
   WHERE e.ontology_version = (SELECT max(version) FROM ${fq}.ontology_versions);

CREATE OR REPLACE VIEW ${fq}.runs_latest
COMMENT 'Current state of each agent run.'
AS SELECT run_id, updated_at, get_json_object(record, '$.task') AS task, get_json_object(record, '$.status') AS status,
          get_json_object(record, '$.outcome') AS outcome, record
   FROM (SELECT *, row_number() OVER (PARTITION BY id ORDER BY updated_at DESC) AS rn FROM ${fq}.runs) WHERE rn = 1;
