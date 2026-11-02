# DephGraph — Where things stand and what is left

*Status as of 2 November 2026, after pulling commit `316c028` ("phases done").*

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
| MySQL database created from `schema.sql` | — | local MySQL 8.4, database `dephgraph` | Done: 23 tables, 3 views, 26 FKs, 7 CHECKs |

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

### 2.1 Decide how the course schema and the app schema relate *(do this before Phase 4)*

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
| `depends_on` | `dependent_*` / `dependency_*`, `is_direct`; PK in MySQL also includes `scope` | `dep_*` / `ref_*`; 4-column PK; `is_direct` lives on `resolves_to` |

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

All on MySQL with `scripts/run-sql.ps1` (output saved under `logs/`).

1. **Fix the DDL** in `er-diagram/schema.sql`:
   - `depends_on` primary key → 4 columns (drop `scope`), as in the relational model and normalization documents
   - add `CHECK (exec_count >= 0)` on `observes` and `CHECK (depth >= 0)` on `resolves_to`
     (documented as domain rules in Phase 3b, not yet enforced)
2. **`sql/seed.sql`:** the Phase 2 sample rows (log4j example), plus enough extra rows for the queries
   to give interesting results.
3. **`sql/constraint_demos.sql`:** run the 12 insert/delete/update cases from Phase 3b and capture the
   real MySQL errors (rejected / cascaded / set NULL). Each one must give the result the document claims.
4. **`sql/queries.sql`:**
   - the 7 relational-algebra queries (Q1–Q7) translated to SQL, with results matching the hand-computed ones
   - the syllabus's "more complex SQL": nested and correlated subqueries, `EXISTS` / `NOT EXISTS`,
     `IN`, `ALL`/`ANY`, outer joins, `GROUP BY` / `HAVING`, views, and the recursive blast-radius CTE
5. **Evidence:** keep the `logs/` output (or Workbench screenshots) for the report.

### 2.3 Phase 5 — Transactions and concurrency (Unit IV)

1. **Transaction design:** show that ingesting one manifest is a single transaction (all rows or none),
   with a demo where a mid-way failure rolls everything back.
2. **ACID:** explain each property with a DephGraph example.
3. **Schedules and serializability:** two concurrent operations (for example, an OSV sync updating
   `affects` while a remediation plan is generated), written as schedules; build the precedence graph,
   test conflict-serializability, show one serial, one serializable and one non-serializable schedule.
4. **Two-phase locking:** run two MySQL sessions side by side to show lock waits, a deadlock and how
   InnoDB resolves it; show `SELECT ... FOR UPDATE`, isolation levels, and lock information from
   `performance_schema.data_locks`.

### 2.4 Phase 6 — NoSQL (Unit V)

The app already uses NoSQL, so this phase can document real code:

1. **Document model:** the app's four MongoDB collections: `raw_manifests`, `osv_advisories`,
   `package_embeddings` and `review_responses` (the LLM's answers at PR-gate stage 3). Helpers are in
   `apps/api/app/models/mongo.py`; the embeddings and review responses are written from the services.
   Explain why each is a document and not a table.
2. **Key-value model:** the Redis cache (blast-radius results with a 24 h TTL).
3. **Aggregates:** what one "aggregate" is in each store.
4. **Distribution:** how MongoDB Atlas (replica set) and Upstash would replicate and shard this data;
   master-slave vs peer-to-peer, sharding plus replication.
5. **Big data:** structured vs semi-structured vs unstructured data in DephGraph; where MapReduce /
   Hadoop would fit at scale.

### 2.5 Report and viva

- Update the synopsis and report with phases 1–6.
- Collect screenshots: ER diagram, MySQL Workbench EER diagram, query outputs, the running dashboard.
- Prepare answers for the known traps: why OBSERVES is ternary, why `fixed_in` needs the full key,
  why basic relational algebra cannot express blast radius, the course-vs-app schema question (2.1).

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

| Gap | Fix |
|---|---|
| CI does not enforce the PRD's ≥ 70 % coverage | add `--cov-fail-under=70` to the pytest step in `.github/workflows/ci.yml` |
| CI does not run the Playwright browser tests | add a job that starts the API and web app and runs `npx playwright test` in the no-Supabase mode described in the README |
| Tests run natively on Windows (outside Docker) fail unless `PYTHONUTF8=1` is set | in `apps/api/tests/conftest.py`, read migrations with `read_text(encoding="utf-8")` |
| Duplicate React keys on the service page (`PrunableTable`): a package with two prunable versions got the same key twice | fixed in the commit that added this file (`key = package_id@version`) |
| React Flow `nodeTypes` warning in development | harmless; can be silenced by memoising, low priority |
| `recharts` 2.x is deprecated | upgrade to 3.x later; not urgent |

---

## 4. MySQL course helpers

The course track runs on a local MySQL 8.4 server, separate from the app's Docker stack.

| File | Use |
|---|---|
| `scripts/run-sql.ps1` | `.\scripts\run-sql.ps1 er-diagram\schema.sql` runs any `.sql` file with the saved MySQL login and writes the output to `logs\` (gitignored) |
| `scripts/verify_mysql_schema.sql` | checks the course database: expects 23 tables, 3 views, 26 foreign keys |

One-time login setup, so no password is ever typed into a script:
`mysql_config_editor set --login-path=dephgraph --host=localhost --user=root --password`

---

## 5. Suggested order

1. Settle 2.1 with your teammate (option A is enough for the course).
2. Course Phase 4 (SQL), then Phase 5 (transactions), then Phase 6 (NoSQL).
3. Production deploy (3.1). It can run in parallel with step 2 if your teammate takes it on.
4. CI gaps (3.2) whenever there is a spare hour.
