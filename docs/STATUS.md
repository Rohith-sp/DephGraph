# DephGraph — Final status

*Final record, 23 November 2026. The course work (Phases 1–6) is complete, and so is the report. The application
is built, merged to `main` and deployed to production. PostgreSQL is the system of record; the MySQL schema
is the course demonstration of the normalised design.*

DephGraph was built on two tracks:

- **Course track (DBMS):** the design and SQL work the syllabus asks for, built on the MySQL schema
  in `er-diagram/schema.sql`.
- **Product track:** the web application in `apps/`, built on the PostgreSQL migrations in
  `apps/api/migrations/`.

---

## 1. Course track

| Phase | Syllabus | Where | Result |
|---|---|---|---|
| 1. ER model | Unit I | [`er-diagram/`](../er-diagram/) | 14 entities (5 weak), 16 relationships, recursive `DEPENDS_ON`, ternary `OBSERVES` |
| 2. ER → relational mapping | Unit II | [`relational-model/`](../relational-model/) | 7-step mapping, 23 relations, 26 foreign keys |
| 3a. Normalization (FDs, minimal cover, 1NF → BCNF) | Unit III | [`normalization/`](../normalization/) | all 23 relations in BCNF, lossless join shown |
| 3b. Constraints and relational algebra | Unit II | [`relational-algebra/`](../relational-algebra/) | 12 update-violation cases, 7 queries, hand-checked |
| MySQL database from `schema.sql` | — | MySQL 8.4 in Docker, database `dephgraph` | 23 tables, 3 views, 26 FKs, 9 CHECKs |
| 4. SQL: DDL, seed, constraint demos, Q1–Q7, complex SQL | Unit III | [`sql/`](../sql/README.md) | completed 21 Nov 2026 |
| 5. Transactions, schedules, 2PL, isolation levels | Unit IV | [`transactions/`](../transactions/README.md) | completed 21 Nov 2026 |
| 6. NoSQL (MongoDB, Redis, replication, sharding) | Unit V | [`docs/COURSEWORK.md`](COURSEWORK.md#phase-6--nosql) | completed 22 Nov 2026 |
| Report and viva guide | — | kept outside the repository | completed 22 Nov 2026 (RVCE format, 20 pages, with a Results & Analysis chapter) |

### Phase 4 — SQL

- `er-diagram/schema.sql`: the `depends_on` primary key has 4 columns (`scope` is a non-key attribute), as
  in the relational model and normalization documents. `CHECK (exec_count >= 0)` on `observes`
  (`chk_exec_count`) and `CHECK (depth >= 0)` on `resolves_to` (`chk_depth`) enforce the Phase 3b domain
  rules; both reject negative values (MySQL error 3819).
- `sql/seed.sql`: exactly the Phase 2 sample rows (the log4j / Log4Shell example), so the hand-checked
  Phase 3b results hold (Q1, Q2, D2 = 11 cascaded rows, the views). The waiver on GHSA-7rjr-3q55-vv33
  expires 2026-11-30; after that date `v_service_exposure` counts 2 open vulnerabilities instead of 1.
- `sql/constraint_demos.sql`: the 12 Phase 3b cases plus X1–X2 (the CHECKs), each in a rolled-back
  transaction; all give the documented result.
- `sql/queries.sql`: Q1–Q7 in SQL; every result matches the hand-computed one.
- `sql/complex_queries.sql` (C1–C25: nested and correlated subqueries, `EXISTS`, `IN`, `ALL`/`ANY`, NULL
  pitfalls, outer joins, `GROUP BY`/`HAVING`, views, `WITH CHECK OPTION`, recursive CTE) runs on extra rows
  from `sql/seed_extra.sql`, so Q1–Q7 keep matching the documents. Real outputs are in
  [`sql/README.md`](../sql/README.md).

### Phase 5 — Transactions

[`transactions/README.md`](../transactions/README.md) has real MySQL output: `sql/tx_ingest_demo.sql`
(atomic ingest) and `scripts/tx_two_sessions.sh` (lock wait, deadlock `ERROR 1213`, dirty and
non-repeatable reads, lost update), schedules with precedence graphs, and two-phase locking. Durability is
explained, not demonstrated. A shorter version is in
[`docs/COURSEWORK.md`](COURSEWORK.md#phase-5--transactions-acid-and-concurrency).

### Phase 6 — NoSQL

[`docs/COURSEWORK.md`](COURSEWORK.md#phase-6--nosql): five MongoDB collections (writers, indexes, TTLs),
the one-way MongoDB to PostgreSQL flow, Redis keys and locks, a live aggregation, and replication, CAP,
sharding and MapReduce explained for DephGraph.

### Course schema and product schema

The coursework MySQL schema and the product PostgreSQL schema are kept separate, and their mapping is
documented in [`er-diagram/README.md`](../er-diagram/README.md#course-schema-and-product-schema). They share
the same 23 core tables and nearly the same keys; the product adds 8 operational tables (31 in total).
Only 3 core tables (`affects`, `package_alias`, `resolves`) have identical columns.

| Table | Course design (ER + MySQL) | Product (PostgreSQL) |
|---|---|---|
| `package` / `package_version` | GROUPED_INTO is on **PACKAGE** (`package.cluster_id`) | `cluster_id` is on **package_version** |
| `scan_run` | independent strong entity, no owner | has `service_id` (scan belongs to a service) |
| `pr_risk_verdict` | `verdict` RISK/WARN/APPROVE + reason, counts, model | `risk_level` PASS/ADVISORY/BLOCK + `stage_reached` |
| `remediation_action` | `action_type`, `target_version`, `est_effort_hrs` | `description`, `status` |
| `depends_on` | `dependent_*` / `dependency_*`, `is_direct`; `scope` is non-key and the PK has four columns | `dep_*` / `ref_*`; 4-column PK; `is_direct` lives on `resolves_to` |

Naming and extra-attribute differences: `team_name` vs `name`, `service_name` vs `name`,
`package_name` vs `name`, `cluster_label` vs `label`, `handle` vs `username`; course-only attributes
such as `team.contact_*`, `service.criticality`, `deployment.is_active`, `observes.first_seen/last_seen`;
product-only attributes such as `team.slug`, `package.description`, `package_version.is_yanked`,
`semantic_cluster.centroid_vec`.

---

## 2. Product track

### Build (PRD phases 0–7)

| PRD phase | Result |
|---|---|
| 0. Setup | Turborepo monorepo, API `/health`, frontend type-check and production build |
| 1. Schema + seed | PostgreSQL migrations `001`–`017` (31 tables), checked by `scripts/verify_schema.py`; local seed data for development only |
| 2. Ingestion | Manifest and lockfile parsing; OSV.dev and deps.dev sync; raw documents in MongoDB first, then one PostgreSQL transaction |
| 3. Analysis | Blast radius, greedy set-cover remediation, prune via runtime evidence |
| 4. Dashboard | Overview, service graph (React Flow), vulnerabilities, remediation plan, clusters, settings |
| 5. Clustering | sentence-transformers → UMAP → HDBSCAN batch job in GitHub Actions |
| 6. PR gate | 3 stages, GitHub Check Runs |
| 7. Production | Hardening and deployment (section 3) |

### Features added for the synopsis (22 November 2026)

| Synopsis item | What was added | Where |
|---|---|---|
| Runtime liveness detection | `POST /runtime/samples`: raw batch to Mongo `runtime_samples` first, then `scan_run` + `observes` in one transaction | `routers/runtime.py`, `services/runtime.py` |
| Maintainer abandonment scoring | `maintainer.last_active_at`; `v_abandonment` = single-maintainer + inactivity (same idea as the MySQL view); registry sync `POST /internal/maintainers-sync` | `migrations/007_maintainer_activity.sql`, `services/maintainers.py` |
| MongoDB raw layer, one-way projection | Ingest stores the raw manifest and advisories in Mongo first, then projects into Postgres; `POST /ingest/replay/{manifest_id}` | `routers/ingest.py`, `services/osv_fetcher.py` |
| PR gate redundancy signal | Stage 2 flags a dependency that does the same job as one the service already uses (same cluster, same ecosystem); offline gate runner | `services/pr_gate_engine.py`, `scripts/pr_gate_dry_run.py` |
| Semantic clustering | Demo data seed; clustering skips packages without a description | `scripts/seed_demo_extra.py`, `jobs/cluster.py` |

The synopsis named a local Ollama model for PR-gate stage 3; the implementation uses Groq
(`openai/gpt-oss-120b`, `max_tokens` 1024).

### Production hardening

| Area | What |
|---|---|
| Reliability | Durable PR-gate delivery queue with retry sweeper (`011`); `scripts/migrate.py` runner with checksums (`--status`, `--dry-run`, `--baseline`) |
| Access | Invite-only: `ORG_ADMIN_EMAILS` create teams, admins invite by email (7-day expiry), verified email read from `auth.users`, optional `ALLOWED_EMAIL_DOMAINS`, GitHub sign-in (`012`) |
| Tenancy | The API enforces team membership on every read and write; RLS policies guard direct database access (PostgREST) |
| Services | Self-serve create, edit and delete; the repository must be reachable by the DephGraph GitHub App; one team per repository + subfolder (`root_path`) |
| GitHub | A push to the default branch imports all manifests as one production deployment; `installation` events import new repositories; App-scoped tokens per repository; cluster dispatch through the App |
| Parsing | `pyproject.toml`, `uv.lock`, `poetry.lock`, `package-lock.json`, `pnpm-lock.yaml`, `yarn.lock`, `Cargo.lock`; lockfile edges are stored per deployment in `deployment_edge` (`014`, `015`), never in the global `depends_on` |
| Alerts | One GitHub Issue per serious CVE reaching a production service, closed on fix; opt-in per team (`013`) |
| Runtime | Python and Node collectors (`collectors/`) with per-service `dgs_` tokens |
| Ops | Rate limit per signed-in user (client IP without a token, default 100/minute); security headers and CSP; pip-audit, npm audit and Dependabot; Sentry (opt-in); free-tier keep-alive; weekly encrypted `pg_dump` backup |
| Checks | `verify_schema.py` and the schema tests derive expectations from the migrations (no literal counts) |

### Tests

CI on `main` (commit `f50e67c`): 744 backend tests passed (4 skipped), 97.66 % coverage (CI fails below
70 %); 46 Playwright end-to-end tests passed; web lint, type check and build clean. `scripts/test_api.sh`
runs the backend tests in Docker with an isolated test database per run. The Playwright job in
`.github/workflows/ci.yml` provisions the test services, applies migrations, seeds the database, starts
the API and runs the browser suite without Supabase; the three real-auth browser tests are skipped in
that mode and run against a local Supabase stack (see the README).

---

## 3. Production deployment (23 November 2026)

Hosted entirely on free tiers. No seed data in production, and development bypasses refuse to run on
Vercel. Steps and settings are in [`DEPLOY.md`](DEPLOY.md#current-deployment-23-november-2026).

| Part | State |
|---|---|
| Web | Vercel project `dephgraph-loki` (Root Directory `apps/web`, `bom1`): https://dephgraph-loki.vercel.app, `/login` answers 200 |
| API | Vercel project `dephgraph-loki-api` (Root Directory `apps/api`, `bom1`): https://dephgraph-loki-api.vercel.app, `/health` 200 in about 0.3 s, `/me` without a token 401 |
| Postgres + Auth | Supabase; GitHub sign-in enabled; Site URL and redirect URL set to the web URL |
| MongoDB / Redis | Atlas M0 and Upstash Redis, both Mumbai, added through the Vercel Marketplace; values copied into `MONGO_URI` / `REDIS_URL` |
| GitHub | App `dephgraph-rohith` (public), webhook to the web route, events Push and Pull request; Actions secrets set for `cron.yml`, `cluster.yml`, `backup.yml` |

Problem found during the deploy and fixed: Vercel detected Turborepo in the API project, ran `npm install`
and `turbo run build`, and never installed the Python packages (`ModuleNotFoundError: No module named
'httpx'` at runtime). Fix: Framework Preset FastAPI, Install Command `pip install -r requirements.txt`, empty
Build Command. This is a required step in `DEPLOY.md` section 4.

---

## 4. MySQL course helpers

The course track runs on a local MySQL 8.4 server, separate from the app's Docker stack.

**Linux / macOS:** MySQL 8.4 runs in its own Docker container (`dephgraph-mysql`, data in the
`dephgraph_mysql_data` volume, port `127.0.0.1:3306`). The root password is generated on first run and
kept only in `~/.config/dephgraph/mysql.cnf` (mode 600), outside the repo.

| Command | Use |
|---|---|
| `scripts/mysql.sh up` | create or start the container |
| `scripts/mysql.sh run er-diagram/schema.sql` | run a `.sql` file; output also saved to `logs/schema.log` (gitignored) |
| `scripts/mysql.sh shell` | interactive `mysql` prompt |
| `scripts/mysql.sh password` | print the root password, for MySQL Workbench |
| `scripts/mysql.sh down` | stop the container (data is kept) |

**Windows:**

| File | Use |
|---|---|
| `scripts/run-sql.ps1` | `.\scripts\run-sql.ps1 er-diagram\schema.sql` runs any `.sql` file with the saved MySQL login and writes the output to `logs\` (gitignored) |
| `scripts/verify_mysql_schema.sql` | checks the course database: expects 23 tables, 3 views, 26 foreign keys |

One-time login setup on Windows, so no password is ever typed into a script:
`mysql_config_editor set --login-path=dephgraph --host=localhost --user=root --password`
