# Claude Handover — GeniusNew Website Database

## Scope

Only the database foundation was built here. Frontend, backend runtime, auth integration, billing provider integration, agent runtime, deployment and UI remain for Claude.

## Neon

- Project: `GeniusNew`
- Project ID: `nameless-cherry-04629009`
- Region: `aws-eu-central-1` (Frankfurt)
- PostgreSQL: 17
- Database: `geniusnew`
- Default branch: `main`
- Default branch ID: `br-wispy-frog-b2kdmmj4`
- History retention: 21600 seconds (Free-plan maximum accepted during setup)

No connection string, database password, token or secret is committed to this repository.

## Migration under review

Canonical repo migration:

`database/migrations/0001_initial_website.sql`

Neon temporary migration branch used for verification:

- Branch: `mcp-migration-2026-09-16T03-41-54`
- Branch ID: `br-empty-cake-b2b6f7bo`
- Migration ID: `80b7f59b-5480-4630-b248-c414f38e76a7`
- Status: tested on temporary branch, not yet promoted to Neon `main` at time of this handover

Verification result on the temporary branch:

- 18 application tables
- 41 foreign-key constraints
- 34 CHECK constraints
- 56 indexes
- schema migration version `0001`
- no raw columns named `password`, `password_hash`, `api_key`, `secret` or `token`

## Data model

The `app` schema provides:

- users
- workspaces
- workspace members and invitations
- projects
- agents and immutable agent versions
- runs and child jobs
- approval requests and decisions
- hashed API-key metadata only
- subscriptions and entitlements
- usage events
- notifications
- file metadata
- audit events with optional hash-chain fields
- schema migration tracking

## Security decisions already made

- Do not store passwords or auth session secrets in application tables.
- `users.auth_subject` is the stable subject from whichever auth provider Claude selects.
- API keys store only `secret_hash` plus a visible prefix; never store the raw key.
- Workspace invitation tokens store only a hash.
- Zero-Trust approval state is represented explicitly in `app.approvals`.
- Audit events are separated from normal run payloads.
- The legacy Agent-Genius database code was not copied.
- No Agent Common data or identifiers were imported.

## Claude next work

1. Select the website stack and ORM. Neon recommends managing schema/migrations as code; if Drizzle is chosen, convert `0001_initial_website.sql` into the canonical Drizzle schema without changing observable database constraints.
2. Select authentication. Neon Managed Auth is available, but any provider is acceptable if its stable user subject maps to `app.users.auth_subject`.
3. Decide whether browser clients will ever access Neon directly. If yes, design and test PostgreSQL RLS before exposing the Data API. If all access is through trusted server code, keep database credentials server-side.
4. Wire signup/login to `app.users`, workspace creation, membership and invitations.
5. Implement CRUD for projects and agent definitions.
6. Implement run/job persistence with idempotent `external_request_id` handling.
7. Implement approval workflows using `pending`, `approved`, `denied`, `expired`, `cancelled` states.
8. Connect billing provider records to subscriptions/entitlements without treating client-submitted plan values as authoritative.
9. Connect object storage and write only file metadata/object keys into `app.files`.
10. Build audit-event generation and define the canonical event-hash algorithm before relying on the optional hash-chain fields.
11. Add integration tests that exercise workspace isolation, ownership, approval boundaries and deletion behavior.
12. Before production, decide on RLS, backup/snapshot policy, staging branches, observability, rate limits and data-retention policy.

## Important boundary

Do not expand this database by importing legacy Agent-Genius tables wholesale. New tables should be justified by the GeniusNew website/runtime contracts and introduced through reviewed migrations.
