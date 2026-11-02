# DephGraph

**Software supply-chain risk intelligence.**

> Every tool tells you which packages have vulnerabilities. DephGraph tells you which ones you can delete, which ones are redundant, and which single upgrade fixes the most services, across the whole organisation.

DephGraph ingests dependency manifests (`requirements.txt`, `package.json`, `Cargo.toml`, `go.mod`) from many services, stores the complete dependency graph in a relational database, and pulls real vulnerability data from [OSV.dev](https://osv.dev) and [deps.dev](https://deps.dev). On top of that graph it answers the questions a security team actually has to act on.

![DephGraph ER diagram](er-diagram/images/er_diagram_full.png)

---

## What it answers

| Question | How |
|---|---|
| **Blast radius**: which services reach this vulnerable package, and through which path? | Recursive CTE over the `depends_on` graph |
| **Prune**: which dependencies ship to production but never execute? | Anti-join between resolved packages and runtime observations |
| **Minimum remediation**: which upgrades clear the most CVEs across the most services? | Greedy set cover |
| **License propagation**: does copyleft code reach a shipped service? | Transitive closure joined with licence data |
| **Consolidate**: which services pull in several packages that do the same job? | Semantic clustering of packages (SBERT, UMAP, HDBSCAN) |
| **PR gate**: is the dependency this pull request adds safe? | 3-stage filter: manifest check, SQL verdict, LLM advisory |

---

## Database design

The core of the project is a 23-table relational schema designed from a Chen ER model.

- 14 entities, 5 of them weak (`deployment`, `package_version`, `remediation_action`, `pr_review`, `pr_risk_verdict`)
- A recursive M:N relationship, `DEPENDS_ON`, on `package_version`
- A ternary relationship, `OBSERVES` (deployment × package version × scan window), for runtime liveness evidence
- Multivalued attributes (`package_alias`, `maintainer_email`) and derived attributes computed as views

The full ER diagrams, the relational mapping and the SQL schema are in [`er-diagram/`](er-diagram/).

---

## Planned tech stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 15, TypeScript, Tailwind CSS, shadcn/ui, ReactFlow, Recharts |
| Backend | FastAPI, SQLAlchemy 2 (async), Pydantic v2 |
| Primary database | PostgreSQL (Supabase); MySQL 8 schema for the DBMS coursework |
| Document store and cache | MongoDB Atlas, Upstash Redis |
| Clustering | sentence-transformers, UMAP, HDBSCAN |
| PR gate LLM | Groq (LLaMA 3.1 70B) |
| Hosting | Vercel (frontend), Render (API), cron-job.org (scheduled syncs) |

---

## Repository layout

```
DephGraph/
├── DephGraph_BUILD_PRD.md     # Product requirements, architecture and build phases
└── er-diagram/
    ├── README.md              # ER model explained: entities, relationships, mapping
    ├── schema.sql             # MySQL 8 schema: 23 tables, views, signature queries
    ├── er_crowsfoot.mmd       # Crow's-foot diagram (Mermaid)
    ├── er_chen.mmd            # Chen diagram (Mermaid)
    ├── er_chen.drawio         # Chen diagram, editable
    ├── er_chen_overview.drawio
    └── images/                # Rendered ER diagrams
└── relational-model/
    ├── README.md              # Phase 2: step-by-step ER-to-relational mapping, sample tables
    └── relational_schema.mmd  # Relational schema diagram (23 relations, PK/FK)
├── normalization/
│   └── README.md              # Phase 3a: FDs, minimal cover, 1NF → BCNF, lossless join
└── relational-algebra/
    └── README.md              # Phase 3b: constraints, update violations, queries in relational algebra
```

---

## Try the schema

```bash
mysql -u root -p < er-diagram/schema.sql
```

This creates the `dephgraph` database with all tables, constraints and views. The five signature queries are at the end of `schema.sql`.

---

## Run locally

Needs Docker, Python 3.12 (with [uv](https://docs.astral.sh/uv/)) and Node 20+.

```bash
docker compose up -d                       # Postgres 15, Mongo 7, Redis 7; first start applies migrations 001-006
# (to re-apply migrations: docker compose down -v, which wipes all local data)
# .env.local (gitignored): defaults already match the compose services, so only secrets are needed; never overwrites an existing file
[ -f .env.local ] || printf 'SUPABASE_JWT_SECRET=dev-secret-at-least-32-characters\nINTERNAL_CRON_SECRET=dev-cron-secret\n' > .env.local

uv venv -p 3.12 apps/api/.venv
uv pip install -p apps/api/.venv -r apps/api/requirements.txt
apps/api/.venv/bin/python scripts/seed_db.py

cd apps/api && .venv/bin/uvicorn app.main:app --reload   # API on :8000
```

In a second terminal:

```bash
npm install
apps/api/.venv/bin/python scripts/make_dev_token.py     # prints a dev JWT
# apps/web/.env.local: AUTH_DISABLED=true and DEV_API_TOKEN=<that token>
npm run dev -w apps/web                                  # dashboard on :3000
```

### Supabase locally (real auth)

Runs the full product against a local Supabase stack (Postgres, Auth, Studio, Mailpit) with real
sign-in. Needs the [Supabase CLI](https://supabase.com/docs/guides/local-development); ports 55320-55329.

```bash
supabase start        # applies apps/api/migrations via the supabase/migrations symlink
supabase status -o env  # keys for .env.local: SUPABASE_URL=http://127.0.0.1:55321, SUPABASE_ANON_KEY,
                        # SUPABASE_SERVICE_ROLE_KEY, SUPABASE_JWT_SECRET, DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55322/postgres
                        # apps/web/.env.local: NEXT_PUBLIC_SUPABASE_URL, NEXT_PUBLIC_SUPABASE_ANON_KEY (leave AUTH_DISABLED unset)
apps/api/.venv/bin/python scripts/demo.py   # reset + seed, creates demo@dephgraph.local, runs the whole product
```

Studio is on http://127.0.0.1:55323, sign-in emails (magic links) in Mailpit on http://127.0.0.1:55324.
The demo calls live OSV, deps.dev and GitHub (unauthenticated, low rate limit; the PR-gate step prints a
note if GitHub refuses). E2E with real auth: build and start the web app, then
`cd apps/web && npx playwright test` with `NEXT_PUBLIC_SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` exported (it
creates the e2e users and logs in through `/login`); `AUTH_DISABLED=true DEV_API_TOKEN=... npx playwright test` is the no-Supabase CI mode.

Tests: `apps/api/.venv/bin/pytest apps/api/tests/`. Deployment: [`docs/DEPLOY.md`](docs/DEPLOY.md).

---

## Status

The design phases and the application build (build phases 0 to 7) are done. See [`DephGraph_BUILD_PRD.md`](DephGraph_BUILD_PRD.md) for the plan.

| Phase | Scope | Status |
|---|---|---|
| Design 1: ER model | Chen ER diagram, 14 entities, 16 relationships | Done |
| Design 2: Relational model | 7-step ER-to-relational mapping, 23 relations ([`relational-model/`](relational-model/)) | Done |
| Design 3a: Normalization | Functional dependencies, minimal cover, 1NF → BCNF, all 23 relations in BCNF ([`normalization/`](normalization/)) | Done |
| Design 3b: Relational algebra | Constraints, update violations, 7 queries in relational algebra ([`relational-algebra/`](relational-algebra/)) | Done |
| Build 0-1: Setup and schema | Monorepo, Postgres schema, RLS, seed data, CI | Done |
| Build 2: Ingestion | Manifest parsing, OSV and deps.dev sync | Done |
| Build 3: Analysis | Blast radius, set cover, prune, licence propagation | Done |
| Build 4: Dashboard | Next.js UI with interactive dependency graph | Done |
| Build 5: Clustering | Semantic package clusters | Done |
| Build 6: PR gate | GitHub Checks integration | Done |
| Build 7: Polish | Rate limiting, structured logging, error envelope, deploy docs ([`docs/DEPLOY.md`](docs/DEPLOY.md)) | Done (cloud deploy not performed) |
