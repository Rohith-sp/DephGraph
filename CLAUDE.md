# DephGraph — Claude Code Root Instructions

## Stack
Turborepo monorepo. `apps/web` = Next.js 15 (Vercel). `apps/api` = FastAPI (Render).
DBs: Supabase PostgreSQL + MongoDB Atlas M0 + Upstash Redis.

## Non-negotiable Rules
1. NEVER edit migration files. Add a new numbered migration (004, 005...).
2. Run `python scripts/verify_schema.py` after ANY schema change.
3. FastAPI: ALWAYS async/await. Zero sync DB calls.
4. Next.js: Server Components default. `"use client"` ONLY for browser APIs/hooks.
5. All FastAPI responses: `{ "data": T | null, "error": string | null }`.
6. Run `pytest apps/api/tests/ -v` before marking backend task done.
7. Run `cd apps/web && npm run build` before marking frontend task done.
8. Never commit secrets. Config via `.env.local` / environment variables only.

## Feature Addition Order (always)
schema migration → SQLAlchemy model → Pydantic schema → router → service → tests → frontend

## Key Paths
- ORM models:     apps/api/app/models/sql.py
- Business logic: apps/api/app/services/
- Migrations:     apps/api/migrations/
- Pages:          apps/web/src/app/(dashboard)/
- Shared types:   apps/web/src/lib/types.ts (auto-generated, don't hand-edit)
