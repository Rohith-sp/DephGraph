# DephGraph — Where things stand and what is left

*Status as of 21 November 2026: course Phases 4 and 5 done on Fedora (MySQL 8.4 in Docker).*

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
| 1. Schema + seed | `scripts/verify_schema.py`, `scripts/seed_db.py` | ✅ 23/23 tables, 5/5 views, 14/14 indexes, RLS 23/23, 27 FKs; 3 teams, 5 services, 50 packages, 200 versions, 15 vulns |
| 2–6. Ingestion, analysis, dashboard, clustering, PR gate | backend test suite | ✅ 140/140 tests pass, 96 % coverage (PRD target ≥ 70 %) |
| 4. Dashboard | every page opened in a browser | ✅ overview, service graph (38 nodes / 67 edges for api-gateway), vulnerabilities, remediation plan, clusters |
| 7. Production deploy | — | ❌ not done |

GitHub Actions CI is also green on the app commits.

---

## 2. Course track — what is left (do these first)

### 2.1 Course and product schema relationship — resolved

Option A was selected: keep the coursework MySQL schema and product PostgreSQL schema separate and
document their mapping. The explanation is now in
[`er-diagram/README.md`](../er-diagram/README.md#course-schema-and-product-schema); neither schema
nor the app migrations were changed.

The two schemas have the same 23 tables and nearly the same keys, but only 3 tables (`affects`,
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
- **B. Change the app to match the course design.** Means new migrations (`007_*.sql`; existing ones
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

The model and distribution write-up is in
[`docs/COURSEWORK.md`](COURSEWORK.md#phase-6--nosql). It records four active MongoDB write paths
and distinguishes the indexed `runtime_samples` collection, which currently has no writer.

No additional NoSQL implementation is required for the coursework; the code-backed explanation is
complete in `docs/COURSEWORK.md`.

### 2.5 Report and viva

- [`docs/COURSEWORK.md`](COURSEWORK.md) now contains report-ready Phase 4–6 explanations and viva
  checkpoints. No separate synopsis/report template was present in the repository.
- Transfer the material into the required report template and add screenshots: ER diagram,
  MySQL Workbench EER diagram, query outputs, and the running dashboard.
- The known viva topics are summarized in the coursework document: ternary `OBSERVES`, full key in
  `AFFECTS`, relational-algebra transitive closure, and the course-vs-app schema relationship.

---

## 3. Product track — what is left

### 3.1 Production deploy (PRD phase 7)

Full instructions are in [`DEPLOY.md`](DEPLOY.md). In order:

| # | Step | Who |
|---|---|---|
| 1 | Create accounts: Supabase, MongoDB Atlas (M0), Upstash, Render, Vercel, Groq, cron-job.org | you (sign-ups need your own login) |
| 2 | **Supabase:** run migrations 001–006 in order, then the seed; copy URL, anon key, JWT secret, pooled `DATABASE_URL` | you, with help |
| 3 | **Security first:** turn off open sign-up in Supabase Auth, or add team scoping. RLS currently lets any signed-in user read everything | you |
| 4 | **MongoDB Atlas:** create the M0 cluster and a database user; copy the SRV string | you |
| 5 | **Upstash:** create a Redis database; copy the `rediss://` URL | you |
| 6 | **Render:** Blueprint from `apps/api/render.yaml`; set the secret variables listed in DEPLOY.md | you, with help |
| 7 | **Vercel:** import the repo, root `apps/web`; set the variables; `AUTH_DISABLED` must stay unset | you, with help |
| 8 | **GitHub App** for the PR gate: `checks:write`, webhook to the Vercel route; app id, private key, webhook secret | you |
| 9 | **Groq** API key (PR gate stage 3) | you |
| 10 | **cron-job.org:** 3 jobs (daily OSV sync, weekly cluster recompute, 10-minute health ping) | you |
| 11 | **Smoke test:** run the PRD phase 7 checks against the live URLs | together |

Keep every key and password out of the repo. They go only in each service's dashboard or a local
`.env.local`.

### 3.2 Smaller gaps

The CI gaps for coverage enforcement and browser testing are now closed in
`.github/workflows/ci.yml`: backend tests fail below 70% coverage, and a Playwright job provisions
the test services, applies migrations/seeds the database, starts the API, and runs the existing
no-Supabase browser suite. Locally, the backend suite passed at 96.31% coverage and the browser suite
passed its three dashboard tests (the three real-auth tests are intentionally skipped in this mode).

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

1. Settle 2.1 with your teammate (option A is enough for the course).
2. Course Phase 6 (NoSQL). Phases 4 and 5 are done.
3. Production deploy (3.1). It can run in parallel with step 2 if your teammate takes it on.
4. CI gaps (3.2) whenever there is a spare hour.
