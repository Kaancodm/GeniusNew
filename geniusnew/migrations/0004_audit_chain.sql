-- Gate B5: exact canonical event bytes and their already signed prefix heads.
-- The independent anchor is never stored in the Core database.

create table public.audit_chain (
    index bigint primary key check (index >= 0),
    previous_hash char(64) not null,
    record_hash char(64) not null unique,
    event bytea not null
);

create table public.audit_heads (
    count bigint primary key check (count > 0),
    version text not null,
    head_hash char(64) not null unique,
    signature bytea not null check (octet_length(signature) = 64),
    created_at bigint not null
);

revoke all on public.audit_chain, public.audit_heads from public;
revoke all on public.audit_chain, public.audit_heads from genius_core;
grant select, insert on public.audit_chain, public.audit_heads to genius_core;
