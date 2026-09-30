-- Gate B1: schema only. Runtime persistence follows in B2/B3.
-- Hash these exact UTF-8 file bytes; never rewrite an applied migration.

create table public.schema_migrations (
    version bigint primary key,
    checksum char(64) not null,
    applied_at bigint not null
);

create table public.job_ledger (
    job_id text primary key,
    subject text not null,
    handoff_sha256 char(64) not null,
    state text not null check (
        state in ('PENDING_APPROVAL', 'RESERVED', 'EXECUTION_COMMITTED', 'COMPLETED', 'REFUSED')
    ),
    created_at bigint not null,
    reserved_at bigint,
    updated_at bigint not null,
    expires_at bigint not null,
    unique (job_id, handoff_sha256),
    check (
        (state = 'PENDING_APPROVAL' and reserved_at is null)
        or (state in ('RESERVED', 'EXECUTION_COMMITTED', 'COMPLETED')
            and reserved_at is not null)
        or state = 'REFUSED'
    )
);

create table public.acceptance_ledger (
    handoff_sha256 char(64) primary key,
    job_id text not null,
    handoff_wire bytea not null,
    result_sha256 char(64) not null unique,
    result_wire bytea not null,
    accepted_at bigint not null,
    foreign key (job_id, handoff_sha256)
        references public.job_ledger(job_id, handoff_sha256)
);

-- A runtime principal must never own these objects or inherit the owner role.
revoke create on schema public from public;
revoke all on schema public from genius_core;
grant usage on schema public to genius_core;
revoke all on public.schema_migrations, public.job_ledger, public.acceptance_ledger from public;
revoke all on public.schema_migrations, public.job_ledger, public.acceptance_ledger from genius_core;
grant select on public.schema_migrations to genius_core;
grant select, insert, update on public.job_ledger to genius_core;
grant select, insert on public.acceptance_ledger to genius_core;

create function public.enforce_job_transition() returns trigger
language plpgsql set search_path = pg_catalog, public as $$
begin
    if (new.job_id, new.subject, new.handoff_sha256, new.created_at, new.expires_at)
       is distinct from
       (old.job_id, old.subject, old.handoff_sha256, old.created_at, old.expires_at) then
        raise exception 'job identity is immutable' using errcode = '23514';
    end if;
    if not (
        (old.state = 'PENDING_APPROVAL' and new.state in ('RESERVED', 'REFUSED'))
        or (old.state = 'RESERVED' and new.state in ('EXECUTION_COMMITTED', 'REFUSED'))
        or (old.state = 'EXECUTION_COMMITTED' and new.state = 'COMPLETED')
    ) then
        raise exception 'invalid job state transition' using errcode = '23514';
    end if;
    if new.reserved_at is distinct from old.reserved_at
       and not (old.state = 'PENDING_APPROVAL' and new.state = 'RESERVED'
                and old.reserved_at is null and new.reserved_at is not null) then
        raise exception 'reservation time is immutable' using errcode = '23514';
    end if;
    return new;
end;
$$;
create trigger job_transition before update on public.job_ledger
    for each row execute function public.enforce_job_transition();

create function public.lock_acceptance_job() returns trigger
language plpgsql set search_path = pg_catalog, public as $$
declare
    job_state text;
begin
    select state into job_state from public.job_ledger
        where job_id = new.job_id and handoff_sha256 = new.handoff_sha256
        for update;
    if job_state is distinct from 'EXECUTION_COMMITTED' then
        raise exception 'acceptance requires the bound committed job' using errcode = '23514';
    end if;
    return new;
end;
$$;
create trigger acceptance_job before insert on public.acceptance_ledger
    for each row execute function public.lock_acceptance_job();

create function public.complete_acceptance_job() returns trigger
language plpgsql set search_path = pg_catalog, public as $$
declare
    changed bigint;
begin
    update public.job_ledger set state = 'COMPLETED', updated_at = new.accepted_at
        where job_id = new.job_id and handoff_sha256 = new.handoff_sha256
        and state = 'EXECUTION_COMMITTED';
    get diagnostics changed = row_count;
    if changed <> 1 then
        raise exception 'acceptance must complete exactly one job' using errcode = '23514';
    end if;
    return new;
end;
$$;
create trigger acceptance_complete after insert on public.acceptance_ledger
    for each row execute function public.complete_acceptance_job();

revoke all on function public.enforce_job_transition(),
    public.lock_acceptance_job(), public.complete_acceptance_job() from public;
