-- Gate B4: append-only approval transitions and their one-way current pointer.
create table public.approval_records (
    token_digest char(64) not null,
    record_hash char(64) not null unique,
    scope bytea not null check (octet_length(scope) <= 16384),
    issued_at bigint not null,
    expires_at bigint not null,
    state text not null check (state in ('GRANTED', 'CONSUMED', 'REVOKED')),
    changed_at bigint not null,
    previous_hash char(64),
    primary key (token_digest, record_hash),
    foreign key (token_digest, previous_hash)
        references public.approval_records(token_digest, record_hash)
);
create unique index approval_one_root_per_token
    on public.approval_records(token_digest) where previous_hash is null;
create unique index approval_one_successor_per_record
    on public.approval_records(token_digest, previous_hash)
    where previous_hash is not null;
create table public.approval_tokens (
    token_digest char(64) primary key,
    current_record_hash char(64) not null,
    foreign key (token_digest, current_record_hash)
        references public.approval_records(token_digest, record_hash)
);

create function public.enforce_approval_record() returns trigger
language plpgsql set search_path = pg_catalog, public as $$
declare
    predecessor public.approval_records%rowtype;
begin
    if new.previous_hash is null then
        if new.state <> 'GRANTED' then
            raise exception 'approval root must be granted' using errcode = '23514';
        end if;
    else
        select * into predecessor from public.approval_records
            where token_digest = new.token_digest and record_hash = new.previous_hash;
        if not found or predecessor.state <> 'GRANTED'
           or new.state not in ('CONSUMED', 'REVOKED') then
            raise exception 'invalid approval transition' using errcode = '23514';
        end if;
        if (new.scope, new.issued_at, new.expires_at)
           is distinct from
           (predecessor.scope, predecessor.issued_at, predecessor.expires_at)
           or new.changed_at < predecessor.changed_at then
            raise exception 'approval scope and lifetime are immutable' using errcode = '23514';
        end if;
    end if;
    if new.state = 'CONSUMED' and new.changed_at >= new.expires_at then
        raise exception 'expired approval cannot be consumed' using errcode = '23514';
    end if;
    return new;
end;
$$;
create trigger approval_record before insert on public.approval_records
    for each row execute function public.enforce_approval_record();

create function public.enforce_approval_pointer() returns trigger
language plpgsql set search_path = pg_catalog, public as $$
declare
    target public.approval_records%rowtype;
begin
    select * into target from public.approval_records
        where token_digest = new.token_digest and record_hash = new.current_record_hash;
    if not found then
        raise exception 'approval pointer target is missing' using errcode = '23514';
    end if;
    if tg_op = 'INSERT' then
        if target.state <> 'GRANTED' or target.previous_hash is not null then
            raise exception 'approval pointer must start at granted root' using errcode = '23514';
        end if;
    else
        if new.token_digest is distinct from old.token_digest
           or target.previous_hash is distinct from old.current_record_hash
           or target.state not in ('CONSUMED', 'REVOKED') then
            raise exception 'approval pointer must move to direct successor' using errcode = '23514';
        end if;
    end if;
    return new;
end;
$$;
create trigger approval_pointer before insert or update on public.approval_tokens
    for each row execute function public.enforce_approval_pointer();

revoke all on public.approval_records, public.approval_tokens from public;
revoke all on public.approval_records, public.approval_tokens from genius_core;
grant select, insert on public.approval_records to genius_core;
grant select, insert, update on public.approval_tokens to genius_core;
revoke all on function public.enforce_approval_record(),
    public.enforce_approval_pointer() from public;
