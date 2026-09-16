# Schema Summary

The initial `app` schema contains 18 tables covering the website's persistent state:

- identity: `users`
- tenancy: `workspaces`, `workspace_members`, `workspace_invitations`
- product: `projects`, `agents`, `agent_versions`
- execution: `runs`, `jobs`, `approvals`
- access: `api_keys`
- commercial: `subscriptions`, `entitlements`, `usage_events`
- experience: `notifications`, `files`
- security: `audit_events`
- migration bookkeeping: `schema_migrations`

Verified on the Neon temporary migration branch:

- 18 tables
- 41 foreign keys
- 34 CHECK constraints
- 56 indexes
- migration version `0001`
- zero columns named `password`, `password_hash`, `api_key`, `secret`, or `token`

Raw credentials, passwords, session secrets, and API keys are intentionally outside this schema.
