# DephGraph — demo runbook (about 15 minutes)

Two parts: **A. the database course work on MySQL** (what is graded), then **B. the running
application on PostgreSQL + MongoDB + Redis**, ending with one live pipeline run. Part B uses the local
Docker stack (it has seed data and a dev token); **C** shows that the same code is also deployed. Every command is run
from the repo root. Expected outputs are shapes, not fixed numbers: counts depend on seed data and live OSV / registry data.

## 0. Setup (30 minutes before)

```bash
git pull
docker compose up -d                       # wait until api and web are "healthy": docker compose ps
scripts/mysql.sh up                        # course MySQL 8.4
scripts/mysql.sh run er-diagram/schema.sql && scripts/mysql.sh run sql/seed.sql   # reset course DB
```

First time on a fresh Docker volume only (the extra demo data and clusters):

```bash
docker run --rm --network dephgraph_default -v "$PWD:/srv/dephgraph:z" -w /srv/dephgraph \
  -e DATABASE_URL=postgresql+asyncpg://dephgraph:dephgraph@postgres:5432/dephgraph \
  dephgraph-api python scripts/seed_demo_extra.py
T=$(docker exec dephgraph-web-1 cat /shared/dev_token)
docker compose --profile ml run --rm cluster   # ~1 min (builds the ~2.3 GB ML image the first time)
```

The `init` container applies pending migrations with `scripts/migrate.py` at every `up`. A volume created before the
runner existed has no migration history; record it once (use the last migration it already had) and `up` does the rest:
`docker compose run --rm init python ../../scripts/migrate.py --baseline 010`

Open in the browser: http://localhost:3000, and the ER figures from `er-diagram/images/`.
Keep two terminals open. In the second one set the variables used in part B:

```bash
T=$(docker exec dephgraph-web-1 cat /shared/dev_token)   # local dev token minted by the stack
H="Authorization: Bearer $T"
```

Dry-run every step once, then reset the course DB again (the two `scripts/mysql.sh run` lines).

## A. Database design and SQL (MySQL), about 7 minutes

| # | Show / run | Say | Expected |
|---|---|---|---|
| A1 | `er-diagram/images/er_chen.png` | 14 entities, 5 weak; recursive DEPENDS_ON; ternary OBSERVES | — |
| A2 | `relational-model/README.md`, the 7-step table | 9 + 5 + 0 + 0 + 6 + 2 + 1 = 23 relations, 26 FKs | — |
| A3 | `normalization/README.md`, flat SCAN_REPORT → BCNF | minimal cover; both methods end at the same tables | — |
| A4 | `scripts/mysql.sh run --force sql/constraint_demos.sql` | each constraint rejects bad data; cascade / set null | `DONE with 10 error(s)`: 1452, 1048, 1062, 3819, 1451; D2 removes 11 rows |
| A5 | `scripts/mysql.sh run sql/queries.sql`, open `logs/queries.log`, Q1b and Q6 | recursive CTE = transitive closure; division with double NOT EXISTS | Q1: payments-svc reaches log4j-core 2.14.1 at level 1; each query prints its expected result first |
| A6 | `scripts/tx_two_sessions.sh deadlock`, then `cat logs/tx_deadlock.log` | opposite lock order → wait-for cycle → InnoDB rolls one back | `ERROR 1213 (40001): Deadlock found…`, `data restored: yes` |

Optional: `scripts/tx_two_sessions.sh lostupdate` (plain SELECT loses an update, FOR UPDATE does not).

## B. The application, about 8 minutes

### B1. Dashboard (browser, http://localhost:3000)

| Page | Say |
|---|---|
| Overview | same 23-table design running on PostgreSQL; counts come from SQL views |
| Services → api-gateway | dependency graph (React Flow); blast radius with the shortest path to each vulnerable package (recursive closure + BFS, cached in Redis 24 h) |
| Vulnerabilities | OSV advisories with CVSS and affected services |
| Remediation → team Platform | greedy set cover: each step = one upgrade, with cumulative coverage % |
| Clusters | NLP: SBERT embeddings → UMAP → HDBSCAN → c-TF-IDF labels; cluster count depends on the data; redundancy = a service using 2+ packages of one cluster |

### B2. Live pipeline (second terminal)

1. **Ingest a manifest: MongoDB first, then PostgreSQL.**

   ```bash
   python3 -c "import json;print(json.dumps({'service_id':1,'ecosystem':'pypi','filename':'requirements.txt','content':open('apps/api/tests/fixtures/requirements_flask.txt').read()}))" > /tmp/ing.json
   curl -s -X POST -H "$H" -H 'Content-Type: application/json' -d @/tmp/ing.json localhost:8000/ingest/manifest
   ```
   Expected: `{"data":{"manifest_id":"…","deploy_seq":N,"packages_parsed":8,…,"vulns_fetched":N,…}}`
   (needs internet: live OSV.dev). Say: raw file stored in MongoDB, advisories stored raw, then one
   PostgreSQL transaction with an advisory lock writes packages, vulnerabilities, the deployment and
   its closure.

2. **Show both sides of the one-way flow.**

   ```bash
   docker exec dephgraph-mongo-1 mongosh dephgraph --quiet --eval 'db.raw_manifests.find({},{raw_content:0,parsed_packages:0}).sort({_id:-1}).limit(1)'
   docker exec dephgraph-postgres-1 psql -U dephgraph dephgraph -c "SELECT service_id, deploy_seq, deployed_at FROM deployment WHERE service_id = 1 ORDER BY deploy_seq DESC LIMIT 2;"
   ```
   Expected: the Mongo document has `status: 'projected'` and the `deploy_seq` that Postgres shows.

3. **Runtime liveness → prunable packages.**

   ```bash
   curl -s -X POST -H "$H" -H 'Content-Type: application/json' localhost:8000/runtime/samples \
     -d '{"service_id":1,"complete":true,"samples":[{"name":"flask","version":"2.0.1","exec_count":120},{"name":"werkzeug","version":"2.0.1","exec_count":80}]}'
   ```
   Expected: `observes_written` 8, `live_packages` 2. Say: packages that never ran are now prunable
   (refresh api-gateway in the dashboard: Prunable list).

4. **PR gate with the LLM stage (Groq).**

   ```bash
   docker run --rm --network dephgraph_default -v "$PWD:/srv/dephgraph:z" -w /srv/dephgraph \
     --env-file ~/.config/dephgraph/groq.env \
     -e DATABASE_URL=postgresql+asyncpg://dephgraph:dephgraph@postgres:5432/dephgraph \
     -e MONGO_URI=mongodb://mongo:27017 -e REDIS_URL=redis://redis:6379/0 \
     dephgraph-api python scripts/pr_gate_dry_run.py --service 1 \
       --dep pypi:httpx:0.27.0 --dep pypi:werkzeug:2.0.1 --dep npm:lodash:4.17.15
   ```
   Expected: httpx ADVISORY (redundant with requests/flask), werkzeug ADVISORY (CVEs + abandonment
   55), lodash PASS (stage 1); with the key, ADVISORY rows backed by CVEs, copyleft or abandonment reach stage 3 with an LLM reason (redundancy-only findings such as httpx stay at stage 2). Say:
   stage 1 = fast exit, stage 2 = SQL rule table (CVSS, copyleft, abandonment, redundancy), stage 3 =
   LLM may only escalate, never relax.

## C. The live deployment, about 1 minute

The production copy runs on Vercel (Mumbai) with Supabase, MongoDB Atlas and Upstash Redis
(details in [`DEPLOY.md`](DEPLOY.md)). It has no seed data, so use it only to show that it is live:

```bash
curl -s -w '  %{http_code} %{time_total}s\n' https://dephgraph-loki-api.vercel.app/health
curl -s -o /dev/null -w '%{http_code}\n' https://dephgraph-loki-api.vercel.app/me
```

Expected: `{"status":"ok"}  200` in well under a second, then `401` (no token, so the API refuses).
Then open https://dephgraph-loki.vercel.app in the browser: the login page, with GitHub sign-in.
Say: two Vercel projects from one monorepo (web and API), invite-only access, schedules and clustering
run in GitHub Actions. Do the full walkthrough on the local stack (part B), not on production.

## If something breaks

| Problem | Do |
|---|---|
| No internet (OSV / Groq) | skip B2.1 and B2.4; show `docs/COURSEWORK.md` §Phase 6 (real stored documents) and report Table 7.3 |
| Dashboard down | `docker compose ps`, `docker compose logs api --tail 30`; show report appendix screenshots |
| MySQL container | `scripts/mysql.sh down && scripts/mysql.sh up`; logs in `logs/*.log` from the dry run |
| Groq error | the gate keeps the stage-2 verdict by design; say so |
| Port busy | set `WEB_PORT`, `API_PORT`, `MYSQL_PORT` before `up` |
| Live site slow or down | first request after idle can be a cold start; retry once, otherwise skip part C |

## Do not show

- Any password, token or key: `scripts/mysql.sh password`, `~/.config/dephgraph/*`, `echo $T`.
- The old ER renders marked "older design" in `er-diagram/README.md`.
- Production data, the Vercel / Supabase / Atlas / Upstash dashboards, or their environment variables.
- Claims that production holds the demo data: the seed and demo scripts run only on the local stack.
