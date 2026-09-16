-- GeniusNew website database
-- Migration: 0001
-- Target: PostgreSQL 17 / Neon
-- Security: no passwords, raw API keys, session tokens, or deployment secrets are stored.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA IF NOT EXISTS app;

CREATE TABLE app.schema_migrations (
  version text PRIMARY KEY,
  description text NOT NULL,
  applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app.users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  auth_subject text NOT NULL UNIQUE,
  email text NOT NULL,
  display_name text,
  avatar_url text,
  locale text NOT NULL DEFAULT 'de-CH',
  timezone text NOT NULL DEFAULT 'Europe/Zurich',
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended','deleted')),
  last_seen_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX users_email_lower_uq ON app.users (lower(email));

CREATE TABLE app.workspaces (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_user_id uuid NOT NULL REFERENCES app.users(id) ON DELETE RESTRICT,
  slug text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$'),
  name text NOT NULL,
  plan_code text NOT NULL DEFAULT 'free',
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended','archived')),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app.workspace_members (
  workspace_id uuid NOT NULL REFERENCES app.workspaces(id) ON DELETE CASCADE,
  user_id uuid NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  role text NOT NULL CHECK (role IN ('owner','admin','member','viewer')),
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('invited','active','suspended')),
  joined_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, user_id)
);
CREATE INDEX workspace_members_user_idx ON app.workspace_members(user_id, status);

CREATE TABLE app.workspace_invitations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES app.workspaces(id) ON DELETE CASCADE,
  email text NOT NULL,
  role text NOT NULL CHECK (role IN ('admin','member','viewer')),
  token_hash text NOT NULL UNIQUE,
  invited_by uuid NOT NULL REFERENCES app.users(id) ON DELETE RESTRICT,
  expires_at timestamptz NOT NULL,
  accepted_at timestamptz,
  revoked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX workspace_invitations_workspace_idx ON app.workspace_invitations(workspace_id, created_at DESC);
CREATE INDEX workspace_invitations_email_lower_idx ON app.workspace_invitations(lower(email));

CREATE TABLE app.projects (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES app.workspaces(id) ON DELETE CASCADE,
  created_by uuid NOT NULL REFERENCES app.users(id) ON DELETE RESTRICT,
  slug text NOT NULL CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$'),
  name text NOT NULL,
  description text,
  visibility text NOT NULL DEFAULT 'private' CHECK (visibility IN ('private','workspace','public')),
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived','deleted')),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, slug)
);
CREATE INDEX projects_workspace_status_idx ON app.projects(workspace_id, status, created_at DESC);

CREATE TABLE app.agents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id uuid NOT NULL REFERENCES app.projects(id) ON DELETE CASCADE,
  created_by uuid NOT NULL REFERENCES app.users(id) ON DELETE RESTRICT,
  slug text NOT NULL,
  name text NOT NULL,
  description text,
  status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','active','paused','archived')),
  current_version_id uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (project_id, slug)
);
CREATE INDEX agents_project_status_idx ON app.agents(project_id, status, created_at DESC);

CREATE TABLE app.agent_versions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id uuid NOT NULL REFERENCES app.agents(id) ON DELETE CASCADE,
  version_no integer NOT NULL CHECK (version_no > 0),
  model_provider text,
  model_name text,
  instructions text,
  config jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_by uuid NOT NULL REFERENCES app.users(id) ON DELETE RESTRICT,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (agent_id, version_no)
);
ALTER TABLE app.agents ADD CONSTRAINT agents_current_version_fk FOREIGN KEY (current_version_id) REFERENCES app.agent_versions(id) ON DELETE SET NULL;
CREATE INDEX agent_versions_agent_idx ON app.agent_versions(agent_id, version_no DESC);

CREATE TABLE app.runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id uuid NOT NULL REFERENCES app.projects(id) ON DELETE CASCADE,
  agent_id uuid NOT NULL REFERENCES app.agents(id) ON DELETE RESTRICT,
  agent_version_id uuid REFERENCES app.agent_versions(id) ON DELETE SET NULL,
  requested_by uuid NOT NULL REFERENCES app.users(id) ON DELETE RESTRICT,
  external_request_id text,
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','pending_approval','succeeded','failed','cancelled','timed_out')),
  input_data jsonb NOT NULL DEFAULT '{}'::jsonb,
  output_data jsonb,
  error_data jsonb,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  prompt_tokens bigint NOT NULL DEFAULT 0 CHECK (prompt_tokens >= 0),
  completion_tokens bigint NOT NULL DEFAULT 0 CHECK (completion_tokens >= 0),
  cost_microunits bigint NOT NULL DEFAULT 0 CHECK (cost_microunits >= 0),
  started_at timestamptz,
  finished_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at)
);
CREATE UNIQUE INDEX runs_external_request_uq ON app.runs(project_id, external_request_id) WHERE external_request_id IS NOT NULL;
CREATE INDEX runs_project_created_idx ON app.runs(project_id, created_at DESC);
CREATE INDEX runs_agent_created_idx ON app.runs(agent_id, created_at DESC);
CREATE INDEX runs_user_created_idx ON app.runs(requested_by, created_at DESC);
CREATE INDEX runs_status_created_idx ON app.runs(status, created_at DESC);

CREATE TABLE app.jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id uuid NOT NULL REFERENCES app.runs(id) ON DELETE CASCADE,
  parent_job_id uuid REFERENCES app.jobs(id) ON DELETE SET NULL,
  worker_key text,
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','pending_approval','succeeded','failed','cancelled','timed_out')),
  priority smallint NOT NULL DEFAULT 50 CHECK (priority BETWEEN 0 AND 100),
  attempt integer NOT NULL DEFAULT 0 CHECK (attempt >= 0),
  max_attempts integer NOT NULL DEFAULT 1 CHECK (max_attempts BETWEEN 1 AND 10),
  input_hash text CHECK (input_hash IS NULL OR input_hash ~ '^[a-f0-9]{64}$'),
  output_hash text CHECK (output_hash IS NULL OR output_hash ~ '^[a-f0-9]{64}$'),
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  started_at timestamptz,
  finished_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at)
);
CREATE INDEX jobs_run_created_idx ON app.jobs(run_id, created_at);
CREATE INDEX jobs_status_created_idx ON app.jobs(status, created_at);

CREATE TABLE app.approvals (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id uuid REFERENCES app.runs(id) ON DELETE CASCADE,
  job_id uuid REFERENCES app.jobs(id) ON DELETE CASCADE,
  requested_by uuid NOT NULL REFERENCES app.users(id) ON DELETE RESTRICT,
  decided_by uuid REFERENCES app.users(id) ON DELETE RESTRICT,
  action_key text NOT NULL,
  risk_level text NOT NULL CHECK (risk_level IN ('low','medium','high','critical')),
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','denied','expired','cancelled')),
  reason text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  requested_at timestamptz NOT NULL DEFAULT now(),
  decided_at timestamptz,
  expires_at timestamptz,
  CHECK (run_id IS NOT NULL OR job_id IS NOT NULL),
  CHECK (decided_at IS NULL OR decided_at >= requested_at)
);
CREATE INDEX approvals_pending_idx ON app.approvals(status, expires_at) WHERE status = 'pending';
CREATE INDEX approvals_run_idx ON app.approvals(run_id, requested_at DESC);

CREATE TABLE app.api_keys (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES app.workspaces(id) ON DELETE CASCADE,
  user_id uuid REFERENCES app.users(id) ON DELETE SET NULL,
  name text NOT NULL,
  key_prefix text NOT NULL,
  secret_hash text NOT NULL UNIQUE,
  scopes text[] NOT NULL DEFAULT ARRAY[]::text[],
  last_used_at timestamptz,
  expires_at timestamptz,
  revoked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX api_keys_workspace_idx ON app.api_keys(workspace_id, created_at DESC);
CREATE INDEX api_keys_prefix_idx ON app.api_keys(key_prefix);

CREATE TABLE app.subscriptions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL UNIQUE REFERENCES app.workspaces(id) ON DELETE CASCADE,
  provider text,
  external_customer_id text,
  external_subscription_id text,
  plan_code text NOT NULL DEFAULT 'free',
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('trialing','active','past_due','cancelled','paused')),
  current_period_start timestamptz,
  current_period_end timestamptz,
  cancel_at_period_end boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX subscriptions_external_customer_uq ON app.subscriptions(provider, external_customer_id) WHERE external_customer_id IS NOT NULL;
CREATE UNIQUE INDEX subscriptions_external_subscription_uq ON app.subscriptions(provider, external_subscription_id) WHERE external_subscription_id IS NOT NULL;

CREATE TABLE app.entitlements (
  workspace_id uuid NOT NULL REFERENCES app.workspaces(id) ON DELETE CASCADE,
  entitlement_key text NOT NULL,
  entitlement_value jsonb NOT NULL DEFAULT 'true'::jsonb,
  source text NOT NULL DEFAULT 'plan',
  valid_until timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, entitlement_key)
);

CREATE TABLE app.usage_events (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  workspace_id uuid NOT NULL REFERENCES app.workspaces(id) ON DELETE CASCADE,
  user_id uuid REFERENCES app.users(id) ON DELETE SET NULL,
  project_id uuid REFERENCES app.projects(id) ON DELETE SET NULL,
  run_id uuid REFERENCES app.runs(id) ON DELETE SET NULL,
  metric text NOT NULL,
  quantity numeric(20,6) NOT NULL CHECK (quantity >= 0),
  unit text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX usage_events_workspace_metric_idx ON app.usage_events(workspace_id, metric, recorded_at DESC);
CREATE INDEX usage_events_run_idx ON app.usage_events(run_id) WHERE run_id IS NOT NULL;

CREATE TABLE app.notifications (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
  workspace_id uuid REFERENCES app.workspaces(id) ON DELETE CASCADE,
  type text NOT NULL,
  title text NOT NULL,
  body text,
  data jsonb NOT NULL DEFAULT '{}'::jsonb,
  read_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX notifications_user_unread_idx ON app.notifications(user_id, created_at DESC) WHERE read_at IS NULL;

CREATE TABLE app.files (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES app.workspaces(id) ON DELETE CASCADE,
  project_id uuid REFERENCES app.projects(id) ON DELETE CASCADE,
  uploaded_by uuid NOT NULL REFERENCES app.users(id) ON DELETE RESTRICT,
  storage_provider text NOT NULL,
  object_key text NOT NULL,
  filename text NOT NULL,
  content_type text,
  size_bytes bigint NOT NULL CHECK (size_bytes >= 0),
  sha256 text CHECK (sha256 IS NULL OR sha256 ~ '^[a-f0-9]{64}$'),
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (storage_provider, object_key)
);
CREATE INDEX files_workspace_created_idx ON app.files(workspace_id, created_at DESC);
CREATE INDEX files_project_created_idx ON app.files(project_id, created_at DESC) WHERE project_id IS NOT NULL;

CREATE TABLE app.audit_events (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  workspace_id uuid NOT NULL REFERENCES app.workspaces(id) ON DELETE RESTRICT,
  project_id uuid REFERENCES app.projects(id) ON DELETE SET NULL,
  actor_user_id uuid REFERENCES app.users(id) ON DELETE SET NULL,
  run_id uuid REFERENCES app.runs(id) ON DELETE SET NULL,
  job_id uuid REFERENCES app.jobs(id) ON DELETE SET NULL,
  approval_id uuid REFERENCES app.approvals(id) ON DELETE SET NULL,
  event_type text NOT NULL,
  actor_type text NOT NULL CHECK (actor_type IN ('user','agent','system','service')),
  actor_id text,
  request_id text,
  source_ip inet,
  user_agent text,
  event_data jsonb NOT NULL DEFAULT '{}'::jsonb,
  previous_hash text CHECK (previous_hash IS NULL OR previous_hash ~ '^[a-f0-9]{64}$'),
  event_hash text CHECK (event_hash IS NULL OR event_hash ~ '^[a-f0-9]{64}$'),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_events_workspace_created_idx ON app.audit_events(workspace_id, created_at DESC);
CREATE INDEX audit_events_project_created_idx ON app.audit_events(project_id, created_at DESC) WHERE project_id IS NOT NULL;
CREATE INDEX audit_events_run_created_idx ON app.audit_events(run_id, created_at DESC) WHERE run_id IS NOT NULL;
CREATE INDEX audit_events_request_idx ON app.audit_events(request_id) WHERE request_id IS NOT NULL;

INSERT INTO app.schema_migrations(version, description) VALUES ('0001', 'Initial GeniusNew website schema');
