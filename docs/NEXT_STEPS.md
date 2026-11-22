# DephGraph — Where things stand and what is left

*Status as of 23 November 2026: course Phases 1–6 done, report written. Product: built, merged to `main` and deployed to production on 23 Nov 2026 (see 3.1). Left: disable open sign-up, rotate the exposed MongoDB password, live acceptance checks, viva. PostgreSQL is the system of record; the MySQL schema is the course demonstration.*

There are two tracks:

- **Course track (DBMS, graded):** design and SQL work the syllabus asks for, built on the MySQL schema
  in `er-diagram/schema.sql`.
- **Product track (PRD):** the actual web application in `apps/`, built on the Postgres migrations in
  `apps/api/migrations/`.

---

## 1. What is done and verified

### Course track

| Phase | Syllabus | Where | State |
|---|---|---|---|
| 1. ER model | Unit I | [`er-diagram/`](../er-diagram/) | Done |
| 2. ER → relational mapping | Unit II | [`relational-model/`](../relational-model/) | Done |
| 3a. Normalization (FDs, minimal cover, 1NF → BCNF) | Unit III | [`normalization/`](../normalization/) | Done |
| 3b. Constraints and relational algebra | Unit II | [`relational-algebra/`](../relational-algebra/) | Done |
| MySQL database created from `schema.sql` | — | MySQL 8.4 in Docker, database `dephgraph` | Done: 23 tables, 3 views, 26 FKs, 9 CHECKs |
| 4. SQL: DDL, seed, constraint demos, Q1–Q7, complex SQL | Unit III | [`sql/`](../sql/README.md) | Done (21 Nov 2026) |
| 5. Transactions, schedules, 2PL, isolation levels | Unit IV | [`transactions/`](../transactions/README.md) | Done (21 Nov 2026) |

### Product track

Checked on a Windows laptop on 2 November 2026, running the API and dashboard natively against local
Postgres, MongoDB and Redis. The team setup is `docker compose up` (README, "Run locally"):

| PRD phase | Check | Result |
|---|---|---|
| 0. Setup | API `/health`, frontend type-check + production build | ✅ `{"status":"ok"}`, build passes (10 routes) |
| 1. Schema + seed | `scripts/verify_schema.py`, `scripts/seed_db.py` | ✅ schema verified by `verify_schema.py` (counts change with every migration; run it for the current numbers); seed data creates demo teams, services and packages only |
| 2–6. Ingestion, analysis, dashboard, clustering, PR gate | backend test suite | ✅ passed at that date; current CI numbers are below the table (CI enforces ≥ 70 % coverage) |
| 4. Dashboard | every page opened in a browser | ✅ overview, service graph (38 nodes / 67 edges for api-gateway), vulnerabilities, remediation plan, clusters |
| 7. Production deploy | `/health`, `/me`, web `/login` on the live URLs | ✅ done 23 Nov 2026 (see 3.1) |

GitHub Actions CI is also green on the app commits. Latest CI on `main` (commit `f50e67c`): 744 backend
tests passed (4 skipped), 97.66 % coverage; 46 Playwright end-to-end tests passed. The app's PostgreSQL
schema now has 31 tables (migrations 001–017: the 23 core tables plus 8 operational ones).

---

### Completion work (22 November 2026)

Everything the synopsis promised is built and tested (CI green; current numbers in section 1). The
synopsis named a local Ollama model for PR-gate stage 3; the implementation uses Groq
(`openai/gpt-oss-120b`, `max_tokens` 1024) instead:

| Synopsis item | What was added | Where |
|---|---|---|
| Runtime liveness detection | `POST /runtime/samples`: raw batch to Mongo `runtime_samples` first, then `scan_run` + `observes` in one transaction | `routers/runtime.py`, `services/runtime.py` |
| Maintainer abandonment scoring | `maintainer.last_active_at`; `v_abandonment` = single-maintainer + inactivity (same idea as the MySQL view); registry sync `POST /internal/maintainers-sync` | `migrations/007_maintainer_activity.sql`, `services/maintainers.py` |
| MongoDB raw layer, one-way projection | Ingest stores the raw manifest and advisories in Mongo first, then projects into Postgres; `POST /ingest/replay/{manifest_id}` | `routers/ingest.py`, `services/osv_fetcher.py` |
| PR gate redundancy signal | Stage 2 flags a dependency that does the same job as one the service already uses (same cluster, same ecosystem); offline gate runner | `services/pr_gate_engine.py`, `scripts/pr_gate_dry_run.py` |
| Semantic clustering target | Demo data seed; clustering skips packages without a description | `scripts/seed_demo_extra.py`, `jobs/cluster.py` |

Verified on the local stack with seed data (counts and timings depend on the seed and are not
claims about production): clustering, a large service graph, a live ingest against OSV.dev, a
runtime batch and a replay. `scripts/test_api.sh` runs the backend tests in Docker with an isolated
test database. Migrations are applied by `scripts/migrate.py` (compose `init` runs it); a volume that predates the
runner needs `scripts/migrate.py --baseline <last applied version>` once.

## 2. Course track — what is left (do these first)

### 2.1 Course and product schema relationship — resolved

Option A was selected: keep the coursework MySQL schema and product PostgreSQL schema separate and
document their mapping. The explanation is now in
[`er-diagram/README.md`](../er-diagram/README.md#course-schema-and-product-schema); neither schema
nor the app migrations were changed.

The two schemas share the same 23 core tables and nearly the same keys (the app adds 8 operational
tables, 31 in total), but only 3 core tables (`affects`,
`package_alias`, `resolves`) have identical columns. In a viva, *"is this the schema you designed?"* needs
one clear answer.

**Structural differences** (they change what the ER diagram says):

| Table | Course design (ER + MySQL) | App (Postgres) |
|---|---|---|
| `package` / `package_version` | GROUPED_INTO is on **PACKAGE** (`package.cluster_id`) | `cluster_id` is on **package_version** |
| `scan_run` | independent strong entity, no owner | has `service_id` (scan belongs to a service) |
| `pr_risk_verdict` | `verdict` RISK/WARN/APPROVE + reason, counts, model | `risk_level` PASS/ADVISORY/BLOCK + `stage_reached` |
| `remediation_action` | `action_type`, `target_version`, `est_effort_hrs` | `description`, `status` |
| `depends_on` | `dependent_*` / `dependency_*`, `is_direct`; `scope` is non-key and the PK has four columns | `dep_*` / `ref_*`; 4-column PK; `is_direct` lives on `resolves_to` |

**Naming and extra-attribute differences:** `team_name` vs `name`, `service_name` vs `name`,
`package_name` vs `name`, `cluster_label` vs `label`, `handle` vs `username`; course-only attributes
such as `team.contact_*`, `service.criticality`, `deployment.is_active`, `observes.first_seen/last_seen`;
app-only attributes such as `team.slug`, `package.description`, `package_version.is_yanked`,
`semantic_cluster.centroid_vec`.

**Options:**

- **A. Keep both, document the mapping (recommended).** Add a section to `er-diagram/README.md`:
  *"The course design is the logical model; the app's physical schema implements it with these
  differences, for these reasons."* Cheapest, honest, no risk to the working app.
- **B. Change the app to match the course design.** Means new migrations (next unused number; existing ones
  must never be edited), ORM, schemas, queries, frontend types and tests. Large, and touches your
  teammate's work, so agree it with them first.
- **C. Change the course design to match the app.** Means redoing the ER diagram, mapping,
  normalization and algebra documents.

### 2.2 Phase 4 — SQL implementation (Unit III, first half)

Run on the course MySQL database with `scripts/mysql.sh` on Linux/macOS or
`scripts/run-sql.ps1` on Windows; output is saved under `logs/`.

1. ✅ **Fix the DDL** in `er-diagram/schema.sql` (done 21 Nov 2026):
   - `depends_on` primary key → 4 columns (`scope` remains a non-key attribute), as in the relational model and normalization documents
   - `CHECK (exec_count >= 0)` on `observes` (`chk_exec_count`) and `CHECK (depth >= 0)` on `resolves_to`
     (`chk_depth`), the Phase 3b domain rules; both verified to reject negative values (MySQL error 3819)
2. ✅ **`sql/seed.sql`** (done 21 Nov 2026): exactly the Phase 2 sample rows (log4j example), so the
   hand-checked Phase 3b results hold (checked: Q1, Q2, D2 = 11 cascaded rows, the views). Re-runnable.
   Any extra rows for the "complex SQL" queries go in a separate file, so they don't change Q1–Q7.
   Note: the waiver on GHSA-7rjr-3q55-vv33 expires 2026-11-30; after that `v_service_exposure`
   counts 2 open vulns instead of 1.
3. ✅ **`sql/constraint_demos.sql`** (done 21 Nov 2026): the 12 Phase 3b cases plus X1–X2 (the new
   CHECKs), each in a rolled-back transaction. All give the documented result. Run with
   `scripts/mysql.sh run --force sql/constraint_demos.sql` (errors are expected); evidence in
   `logs/constraint_demos.log`.
4. ✅ **`sql/queries.sql`** (done 21 Nov 2026): Q1–Q7 in SQL on the base sample; every result matches
   the hand-computed one. The "more complex SQL" (25 queries, C1–C25: nested and correlated
   subqueries, `EXISTS`, `IN`, `ALL`/`ANY`, NULL pitfalls, outer joins, `GROUP BY`/`HAVING`, views,
   `WITH CHECK OPTION`, recursive CTE) is in `sql/complex_queries.sql`, on extra rows from
   `sql/seed_extra.sql`, so Q1–Q7 keep matching the document. Write-up with real outputs:
   [`sql/README.md`](../sql/README.md).
5. **Evidence:** outputs are pasted into `sql/README.md`; still to do: Workbench screenshots for the report.

### 2.3 Phase 5 — Transactions and concurrency (Unit IV) ✅ done 21 Nov 2026

All four items below are done in [`transactions/README.md`](../transactions/README.md), with real
MySQL output: `sql/tx_ingest_demo.sql` (atomic ingest) and `scripts/tx_two_sessions.sh` (lock wait,
deadlock `ERROR 1213`, dirty / non-repeatable reads, lost update). Durability is explained, not demonstrated.

A shorter version of the explanation, schedules, ACID mapping and two-session lock/deadlock instructions is in
[`docs/COURSEWORK.md`](COURSEWORK.md#phase-5--transactions-acid-and-concurrency). The rollback demo
was run and verified. Separate MySQL sessions also demonstrated a row-lock wait and an InnoDB
deadlock (`ERROR 1213`); the winning transaction rolled back without changing sample values. The
`performance_schema.data_locks` and `data_lock_waits` queries were also checked while a transaction
was waiting. Only submission-specific screenshots/transcripts remain; the rollback proof and
written explanations are complete.

### 2.4 Phase 6 — NoSQL (Unit V)

Status: done, 22 Nov 2026. Write-up in [`docs/COURSEWORK.md`](COURSEWORK.md#phase-6--nosql):
five Mongo collections (writers, indexes, TTLs), the one-way Mongo to Postgres flow, Redis keys and
lock, a live aggregation, and replication / CAP / sharding / MapReduce marked as design only.
Optional later: sample documents for four collections that are empty on the local stack.

### 2.5 Report and viva ✅ report done 22 Nov 2026

- The report follows `DBMS-ReportFormat - 2025-26.docx` with an extra Results & Analysis chapter;
  20 numbered pages. It and the viva guide are kept outside the repo (`DephGraph_Report.docx`,
  `DephGraph_Technical_Guide.docx`).
- Still to do: viva rehearsal; MySQL Workbench screenshots only if the faculty asks for them.

---

## 3. Product track — what is left

### 3.1 Production deploy ✅ done 23 November 2026

Target: an internal tool for one organisation, hosted entirely on free tiers. Nothing simulated ships: no
seed data in production, and dev bypasses refuse to run on Vercel. Steps and settings are in
[`DEPLOY.md`](DEPLOY.md#current-deployment-23-november-2026).

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
Build Command. This is now a required step in `DEPLOY.md` section 4.

The production build work that used to be listed here (durable PR-gate deliveries, invite-only access,
self-serve services, push imports, lockfile parsers, alert issues, runtime collectors, security headers,
backups, migration runner) is merged into `main` and is what is deployed.

**Left, in order:**

1. Supabase: turn off open sign-up (Authentication > Email > "Allow new users to sign up").
2. Rotate the MongoDB Atlas database user's password, which was exposed during setup, then update
   `MONGO_URI` on the API project and in the GitHub Actions secrets.
3. Live acceptance on production with this repo: GitHub sign-in, create team, invite, add service,
   push to main imports, PR gets a Check Run, collector sample lands, alert issue opens and closes,
   backup artifact restores.

### 3.2 Smaller gaps

The CI gaps for coverage enforcement and browser testing are now closed in
`.github/workflows/ci.yml`: backend tests fail below 70% coverage, and a Playwright job provisions
the test services, applies migrations/seeds the database, starts the API, and runs the existing
no-Supabase browser suite. The three real-auth browser tests are intentionally skipped in that no-Supabase mode.
Current numbers are in section 1.

| Gap | Fix |
|---|---|
| Tests run natively on Windows (outside Docker) fail unless `PYTHONUTF8=1` is set | in `apps/api/tests/conftest.py`, read migrations with `read_text(encoding="utf-8")` |
| Duplicate React keys on the service page (`PrunableTable`): a package with two prunable versions got the same key twice | fixed in the commit that added this file (`key = package_id@version`) |
| React Flow `nodeTypes` warning in development | harmless; can be silenced by memoising, low priority |
| `recharts` 2.x is deprecated | upgrade to 3.x later; not urgent |

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

---

## 5. Suggested order

1. Disable open sign-up and rotate the MongoDB password (3.1, items 1–2).
2. Viva rehearsal ([`DEMO.md`](DEMO.md), now with a short live-deployment part).
3. Live acceptance checks on production (3.1, item 3), if time allows.
