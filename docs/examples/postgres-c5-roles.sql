-- Review-only: run with psql -X -v ON_ERROR_STOP=1 in the dedicated C5 DB.
-- Approved bootstrap only; roles and migrations 0001..0004 must already exist.
-- The bootstrap administrator executes this, never the runtime or backup role.
BEGIN;

-- One live service connection, not a pool or a security-grade singleton lock.
ALTER ROLE genius_core CONNECTION LIMIT 1;
ALTER ROLE genius_core SET search_path = pg_catalog, public;
ALTER ROLE genius_core SET statement_timeout = '10s';
ALTER ROLE genius_core SET lock_timeout = '5s';
ALTER ROLE genius_core SET idle_in_transaction_session_timeout = '60s';

-- Database-level rights are separate from migration-owned table grants.
REVOKE CONNECT, CREATE ON DATABASE geniusnew FROM PUBLIC;
REVOKE TEMPORARY ON DATABASE geniusnew FROM PUBLIC;
REVOKE CREATE, TEMPORARY ON DATABASE geniusnew FROM genius_core, genius_backup;
GRANT CONNECT ON DATABASE geniusnew TO genius_migrate, genius_core, genius_backup;

-- Explicit inventory: re-evaluate after each approved schema change.
GRANT USAGE ON SCHEMA public TO genius_backup;
GRANT SELECT ON public.schema_migrations, public.job_ledger,
    public.acceptance_ledger, public.pending_jobs, public.approval_records,
    public.approval_tokens, public.audit_chain, public.audit_heads TO genius_backup;
ALTER ROLE genius_backup SET default_transaction_read_only = on;

-- Install only after the application migrations, in a separate locked schema.
-- This is a proposed bootstrap operation, not a GeniusNew schema migration.
CREATE SCHEMA c5_stats;
REVOKE ALL ON SCHEMA c5_stats FROM PUBLIC;
CREATE EXTENSION pg_stat_statements WITH SCHEMA c5_stats;
REVOKE ALL ON ALL TABLES IN SCHEMA c5_stats FROM PUBLIC;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA c5_stats FROM PUBLIC;
GRANT USAGE ON SCHEMA c5_stats TO genius_backup;
GRANT EXECUTE ON FUNCTION c5_stats.pg_stat_statements(boolean) TO genius_backup;

COMMIT;
