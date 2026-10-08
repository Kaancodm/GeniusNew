-- Read-only output allowlist; run as genius_backup, not genius_core.
-- Use psql -X -v ON_ERROR_STOP=1, without statement echo or DSN output.
BEGIN READ ONLY;
SET LOCAL statement_timeout = '5s';
SET LOCAL lock_timeout = '1s';

-- Missing extension/preload or an unavailable DB is a monitoring error, not zero.
SHOW server_version_num;
SHOW data_checksums;

SELECT count(*) AS audit_records,
       count(*) >= 43496 AS audit_capacity_warning
FROM public.audit_chain;

-- No user, principal, token, query text, query ID or payload leaves this query.
-- Other users' numeric statistics do not require pg_read_all_stats.
SELECT coalesce(sum(calls), 0) AS calls,
       coalesce(sum(total_exec_time), 0) AS total_exec_ms,
       coalesce(sum(total_exec_time) / nullif(sum(calls), 0), 0) AS mean_exec_ms,
       coalesce(max(max_exec_time), 0) AS max_exec_ms,
       coalesce(sum(shared_blks_read), 0) AS shared_blks_read,
       coalesce(sum(shared_blks_hit), 0) AS shared_blks_hit,
       coalesce(sum(temp_blks_written), 0) AS temp_blks_written,
       coalesce(sum(wal_bytes), 0) AS wal_bytes
FROM c5_stats.pg_stat_statements(false)
WHERE dbid = (SELECT oid FROM pg_catalog.pg_database
              WHERE datname = current_database())
  AND userid = (SELECT oid FROM pg_catalog.pg_roles WHERE rolname = 'genius_core')
  AND toplevel;

-- Fixed schema names are labels, never values taken from application rows.
SELECT c.relname AS table_name,
       pg_catalog.pg_relation_size(c.oid) AS heap_bytes,
       pg_catalog.pg_indexes_size(c.oid) AS index_bytes,
       pg_catalog.pg_total_relation_size(c.oid) AS total_bytes
FROM pg_catalog.pg_class AS c
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relname IN ('schema_migrations', 'job_ledger', 'acceptance_ledger',
                   'pending_jobs', 'approval_records', 'approval_tokens',
                   'audit_chain', 'audit_heads')
ORDER BY c.relname;
COMMIT;
