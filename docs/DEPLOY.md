# Deploying DephGraph

Target: Vercel Hobby (two projects from this monorepo, region `bom1` = Mumbai), Supabase (Postgres + Auth),
MongoDB Atlas M0, Upstash Redis, GitHub Actions for schedules and clustering. Put the three databases in
Mumbai (`ap-south-1`) too where the free tier offers it; check each console's region picker, a far-away
database adds its round-trip time to every query. Vercel Hobby is for personal, non-commercial use.
Every variable is documented in [`.env.example`](../.env.example).

Order: 1 Supabase, 2 Atlas, 3 Upstash, 4 Vercel API project, 5 Vercel web project, 6 GitHub (secrets, App,
token), 7 smoke test. The API URL is needed for the web project and for GitHub, so deploy the API first.

## 1. Supabase (Postgres + auth)

Apply every file in `apps/api/migrations/` in numeric order, each once, in the SQL editor or with
`psql -v ON_ERROR_STOP=1 -f` (`for f in apps/api/migrations/*.sql` sorts correctly; today that is 001 through 010).
Later deploys apply only the files added since. Existing migrations are immutable; schema changes go in a new
file with the next unused number.

Access is per team (`team_member`): the API limits every read and write to the caller's teams and the RLS
policies in `010` mirror that. Disable open sign-up (Auth > Providers > Email > "Allow new users to sign up")
and add people with `python scripts/add_team_member.py --team-slug <slug> --user-id <uuid>` (or `--email`).

New projects sign tokens with ES256/RS256; the API verifies them against
`{SUPABASE_URL}/auth/v1/.well-known/jwks.json`. `SUPABASE_JWT_SECRET` is only for legacy HS256 projects.

Two connection strings, both with the `postgresql+asyncpg://` scheme (Project > Connect > Shared pooler):

| Used by | Mode | Port | Why |
|---|---|---|---|
| Vercel API project | **transaction** pooler | 6543 | serverless instances come and go; a pooler keeps the connection count low. The API detects Vercel (`VERCEL=1`) and turns off asyncpg's prepared-statement cache, which transaction mode cannot serve |
| GitHub Actions secret, local, Docker | **session** pooler (or direct) | 5432 | the clustering job holds transactions and prepared statements; the direct host is IPv6-only on the free plan and GitHub runners are IPv4, so use the pooler host |

The pooled user is `postgres.<project-ref>`.

## 2. MongoDB Atlas and 3. Upstash

Atlas M0: create the cluster and a database user, allow `0.0.0.0/0` in Network Access (Vercel has no fixed
egress IP), copy the SRV string into `MONGO_URI`. Indexes are created idempotently at every cold start.
Upstash: create a Redis database, copy the `rediss://default:<token>@<host>:6379` URL into `REDIS_URL`.
Free limit: 500K commands per month. An authenticated read costs about four (rate limit, data generation,
membership, cached payload), so roughly 100K requests per month; the cache and the limiter degrade to
"compute directly" / in-memory counters if Redis is exhausted or down.

## 4. Vercel project "API"

Import the repo, **Root Directory `apps/api`**, framework preset Other/FastAPI. Vercel detects `app/main.py`
(top-level `app`), installs `requirements.txt` (slim: no torch), uses Python 3.12 (`.python-version`) and
reads `apps/api/vercel.json` (region `bom1`, `maxDuration` 300 s, tests/migrations/jobs excluded from the
bundle). One function serves every route, so the 300 s Hobby maximum applies to all of them.

| Variable | Value |
|---|---|
| `DATABASE_URL` | Supabase **transaction** pooler (6543), `postgresql+asyncpg://...` |
| `MONGO_URI` | Atlas SRV string (`MONGO_DB_NAME` optional, default `dephgraph`) |
| `REDIS_URL` | Upstash `rediss://...` |
| `SUPABASE_URL` | Supabase project URL (JWKS source) |
| `SUPABASE_JWT_SECRET` | legacy HS256 projects only |
| `GROQ_API_KEY` | Groq key (PR gate stage 3); `GROQ_MODEL` optional |
| `GITHUB_APP_ID`, `GITHUB_PRIVATE_KEY_BASE64`, `GITHUB_WEBHOOK_SECRET` | GitHub App; key is the PEM, base64-encoded |
| `INTERNAL_CRON_SECRET` | long random string; guards `/internal/*` |
| `CORS_ORIGINS` | JSON list, e.g. `["https://dephgraph.vercel.app"]` |
| `GITHUB_DISPATCH_TOKEN` | fine-grained token for this repo with **Actions: read and write**; lets `POST /clusters/recompute` start `cluster.yml` |
| `GITHUB_REPOSITORY` | `owner/name` of this repo |
| `ENVIRONMENT` | `production` (JSON logs) |

Optional: `RATE_LIMIT` (default `100/minute`), `TRUSTED_PROXY_HOPS` (default `1`). Vercel sets `VERCEL=1` itself.

Rate limiting is per authenticated user (verified JWT `sub`), else per client IP, as a fixed window in Redis
shared by all instances (in-memory fallback when Redis errors). Vercel overwrites `X-Forwarded-For` with the
client's IP and does not forward client-supplied values, so the default `TRUSTED_PROXY_HOPS=1` is right;
raise it only if you add another proxy. `/health` and `/pr-gate/webhook` are exempt.

Vercel cannot run work after a Python response, so clustering and the schedules do not run in the API. Without
`GITHUB_DISPATCH_TOKEN`/`GITHUB_REPOSITORY`, `POST /clusters/recompute` answers `503`.

## 5. Vercel project "web"

Import the same repo again, **Root Directory `apps/web`** (`vercel.json`: region `bom1`).

| Variable | Value |
|---|---|
| `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase project URL and anon key |
| `NEXT_PUBLIC_API_URL` | the API project's URL |
| `API_URL` | optional server-side override of the API base |
| `GITHUB_WEBHOOK_SECRET` | same value as on the API project |

**`AUTH_DISABLED` must be unset or `false` in production.** `true` skips the login middleware and sends
`DEV_API_TOKEN` to the API. Leave `DEV_API_TOKEN` unset too, and never put `SUPABASE_SERVICE_ROLE_KEY` on Vercel.

The GitHub App's webhook URL is `https://<web project>/api/webhooks/github`. The route verifies the signature,
answers GitHub `202` at once and forwards the delivery to the API inside Next's `after()` (Vercel `waitUntil`,
up to 300 s). The API reviews the PR synchronously and answers `200` with `{verdict, stage_reached,
processing_ms, deps}`, which the route only logs; GitHub sees the result as a Check Run.

## 6. GitHub: Actions secrets

Repo > Settings > Secrets and variables > Actions. Workflows skip with a notice when their secrets are missing.

| Secret | Used by | Value |
|---|---|---|
| `DATABASE_URL` | cluster.yml | Supabase **session** pooler (5432), `postgresql+asyncpg://...` |
| `MONGO_URI`, `MONGO_DB_NAME` (optional) | cluster.yml | as on the API project |
| `REDIS_URL` | cluster.yml | as on the API project (the job takes the same lock the API checks) |
| `API_BASE_URL` | cron.yml | the API project's URL, no trailing path |
| `INTERNAL_CRON_SECRET` | cron.yml | same value as on the API project |

| Workflow | Schedule (UTC) | Does |
|---|---|---|
| `cron.yml` | daily 02:23 / Mondays 03:41 | `POST /internal/osv-sync` / `/internal/maintainers-sync` with `X-Cron-Secret`; also runnable by hand |
| `cluster.yml` | daily 03:17 + on dispatch | runs `python -m app.jobs.cluster` (CPU torch, model cached) on a free 4 vCPU runner; one run at a time |

On public repos GitHub disables scheduled workflows after 60 days without repository activity; re-enable them in
the Actions tab. Scheduled runs can start several minutes late.

## 7. Smoke test

```bash
BASE=https://<api project>.vercel.app
curl $BASE/health
curl -X POST $BASE/internal/osv-sync -H "X-Cron-Secret: $INTERNAL_CRON_SECRET"
curl $BASE/services -H "Authorization: Bearer $TOKEN"
curl -X POST $BASE/clusters/recompute -H "Authorization: Bearer $TOKEN"   # {"status":"dispatched"}; watch the Actions tab
```

Every response carries an `X-Request-ID` header; the same id is on that request's log lines.
Hobby keeps runtime logs for one hour.

## Self-hosting

`docker compose up -d --build --wait` runs the same code (slim API image, uvicorn). Clustering:
`docker compose --profile ml run --rm cluster`. See the README.
