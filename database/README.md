# GeniusNew Database

This directory contains the reviewed database foundation for the GeniusNew website.

- Provider: Neon / PostgreSQL 17
- Canonical schema namespace: `app`
- Initial migration: `migrations/0001_initial_website.sql`
- Claude continuation notes: `CLAUDE-HANDOFF.md`

Rules:

- Never commit database passwords, connection strings, tokens or raw API keys.
- Never copy legacy Agent-Genius database code wholesale.
- Every schema change must be represented by a reviewed migration.
- Production promotion must follow Neon branch-first migration testing.
- Authentication secrets belong to the selected auth system, not the application tables.
