-- Gate B4: exact pending wires, bound to the permanent job ledger.
create table public.pending_jobs (
    job_id text primary key references public.job_ledger(job_id),
    subject text not null,
    wire bytea not null,
    trace_id text not null,
    expires_at bigint not null
);
revoke all on public.pending_jobs from public;
revoke all on public.pending_jobs from genius_core;
grant select, insert, delete on public.pending_jobs to genius_core;
