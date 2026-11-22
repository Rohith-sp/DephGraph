# Deploying DephGraph

Target: Vercel Hobby (two projects from this monorepo, region `bom1` = Mumbai), Supabase (Postgres + Auth),
MongoDB Atlas M0, Upstash Redis, GitHub Actions for schedules and clustering. Put the three databases in
Mumbai (`ap-south-1`) too where the free tier offers it; check each console's region picker, a far-away
database adds its round-trip time to every query. Vercel Hobby is for personal, non-commercial use.
Every variable is documented in [`.env.example`](../.env.example).

## Current deployment (23 November 2026)

| Part | Where |
|---|---|
| Web (Vercel project `dephgraph-loki`, Root Directory `apps/web`, region `bom1`) | https://dephgraph-loki.vercel.app |
| API (Vercel project `dephgraph-loki-api`, Root Directory `apps/api`, region `bom1`) | https://dephgraph-loki-api.vercel.app |
| Postgres + Auth | Supabase (GitHub sign-in on; Site URL and redirect URL = the web URL) |
| MongoDB | Atlas M0, Mumbai, added through the Vercel Marketplace integration on the API project |
| Redis | Upstash (free), Mumbai, added through the Vercel Marketplace integration on the API project |
| GitHub App | `dephgraph-rohith`, public; webhook `https://dephgraph-loki.vercel.app/api/webhooks/github`; events Push and Pull request |
| Schedules | GitHub Actions secrets set; `cron.yml`, `cluster.yml` and `backup.yml` use them |

Checked after the deploy: API `/health` answers `200 {"status":"ok"}` in about 0.3 s, `/me` without a token
answers `401`, and the web `/login` page answers `200`.

Order: 1 Supabase, 2 Atlas, 3 Upstash, 4 Vercel API project, 5 Vercel web project, 6 GitHub (secrets, App,
token), 7 smoke test. The API URL is needed for the web project and for GitHub, so deploy the API first.

## 1. Supabase (Postgres + auth)

Apply the migrations with the runner, from a machine that can reach the database (use the **session** pooler or
direct connection below, not the transaction pooler: the run holds an advisory lock):

```bash
DATABASE_URL=postgresql+asyncpg://postgres.<ref>:<password>@<pooler-host>:5432/postgres python scripts/migrate.py
```

It applies `apps/api/migrations/*.sql` in order, each in its own transaction, and records them in
`schema_migrations` with a checksum. Re-running applies only new files, and it refuses to run if an applied file was
edited: schema changes go in a new file with the next unused number. `--status` and `--dry-run` change nothing.
A database that was set up by hand with 001..N (SQL editor, psql) needs `--baseline N` once, which records those
files without running them after checking their tables exist. `python scripts/verify_schema.py` checks the result.

Access is per team (`team_member`): the API checks membership itself and limits every read and write to the
caller's teams. The RLS policies in `010` mirror that rule and guard direct database access (Supabase PostgREST). Disable open sign-up (Auth > Providers > Email > "Allow new users to sign up")
and add people with `python scripts/add_team_member.py --team-slug <slug> --user-id <uuid>` (or `--email`).

New projects sign tokens with ES256/RS256; the API verifies them against
`{SUPABASE_URL}/auth/v1/.well-known/jwks.json`. `SUPABASE_JWT_SECRET` is only for legacy HS256 projects.

For GitHub sign-in set Authentication > URL Configuration: Site URL = the web URL, and add the web URL to the
redirect URLs (see also "Access model" below).

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

**Using the Vercel Marketplace integrations (what the live deploy did).** Atlas and Upstash can both be added
from the API project's Storage tab. The integrations create their own variables (`MONGODB_URI`, and
`UPSTASH_REDIS_*` such as `UPSTASH_REDIS_REDIS_URL`), but the app reads only **`MONGO_URI`** and **`REDIS_URL`**.
Copy the values into those two names yourself:

- `MONGO_URI` = the value of `MONGODB_URI`.
- `REDIS_URL` = the `rediss://` URL (`UPSTASH_REDIS_REDIS_URL`), **not** the REST `https://` URL.
- To read a value, turn "Sensitive" off on the integration's variables, or copy it from the store's
  Quickstart ("Show secret") page.
- Still allow `0.0.0.0/0` in Atlas Network Access.
Free limit: 500K commands per month. The limiter costs two commands per request (one `EVAL` that runs `INCR` and
`EXPIRE`) and a cached read one more, so roughly 150K requests per month. Cache generations (per team, one for the
shared catalogue, one for memberships) and memberships are also held in each instance's memory for 1.5 s, so a burst
of requests shares one read. A write on one instance reaches the others within that 1.5 s. The cache and the limiter
degrade to "compute directly" / in-memory counters if Redis is exhausted or down.

## 4. Vercel project "API"

Import the repo, **Root Directory `apps/api`**. Vercel detects `app/main.py` (top-level `app`), uses Python 3.12
(`.python-version`) and reads `apps/api/vercel.json` (region `bom1`, `maxDuration` 300 s, tests/migrations/jobs
excluded from the bundle). One function serves every route, so the 300 s Hobby maximum applies to all of them.

**Required: override the build settings** (Settings > Build and Deployment). Vercel sees the Turborepo at the
repo root and otherwise runs `npm install` and `turbo run build` for this project, so the Python packages are
never installed and every request fails with `ModuleNotFoundError: No module named 'httpx'`. Set:

| Setting | Value |
|---|---|
| Framework Preset | FastAPI |
| Install Command (override on) | `pip install -r requirements.txt` (slim: no torch) |
| Build Command (override on) | empty |

Redeploy after changing them.

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
| `CORS_ORIGINS` | JSON list, e.g. `["https://dephgraph-loki.vercel.app"]` |
| `WEB_URL` | web project URL; alert issues link to its vulnerability page |
| `GITHUB_REPOSITORY` | `owner/name` of this repo |
| `ENVIRONMENT` | `production` (JSON logs) |

Optional: `SENTRY_DSN` (errors are reported only when set; request bodies, cookies and the `Authorization`,
`X-Hub-Signature-256` and `X-Cron-Secret` headers are stripped) and `SENTRY_TRACES_SAMPLE_RATE` (default `0.1`),
`RATE_LIMIT` (default `100/minute`), `TRUSTED_PROXY_HOPS` (default `1`). Vercel sets `VERCEL=1` itself.

Rate limiting is per signed-in user (verified JWT `sub`), or per client IP for requests without a token (default 100/minute), as a fixed window in Redis
shared by all instances (in-memory fallback when Redis errors). Vercel overwrites `X-Forwarded-For` with the
client's IP and does not forward client-supplied values, so the default `TRUSTED_PROXY_HOPS=1` is right;
raise it only if you add another proxy. `/health` and `/pr-gate/webhook` are exempt.

Vercel cannot run work after a Python response, so clustering and the schedules do not run in the API. Without
the GitHub App (Actions: write) installed on `GITHUB_REPOSITORY`, `POST /clusters/recompute` answers `503`.

## 5. Vercel project "web"

Import the same repo again, **Root Directory `apps/web`** (`vercel.json`: region `bom1`).

| Variable | Value |
|---|---|
| `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase project URL and anon key |
| `NEXT_PUBLIC_API_URL` | the API project's URL |
| `API_URL` | optional server-side override of the API base |
| `GITHUB_WEBHOOK_SECRET` | same value as on the API project |
| `SENTRY_DSN` / `NEXT_PUBLIC_SENTRY_DSN` | optional: server errors / browser errors (the public one is also allowed in the CSP `connect-src`); `SENTRY_TRACES_SAMPLE_RATE` for the server |

**`AUTH_DISABLED` must be unset or `false` in production.** `true` skips the login middleware and sends
`DEV_API_TOKEN` to the API. Leave `DEV_API_TOKEN` unset too, and never put `SUPABASE_SERVICE_ROLE_KEY` on Vercel.

The GitHub App's webhook URL is `https://<web project>/api/webhooks/github`. The route verifies the signature,
answers GitHub `202` at once and forwards the delivery to the API inside Next's `after()` (Vercel `waitUntil`,
up to 300 s). The API reviews the PR synchronously and answers `200` with `{verdict, stage_reached,
processing_ms, deps}`, which the route only logs; GitHub sees the result as a Check Run.

Deliveries are durable once they reach the API: it stores each `pull_request`, `push` and installation delivery (`pr_gate_delivery`, keyed by
`X-GitHub-Delivery`) before reviewing it. A redelivery of the same id is a no-op (`{"duplicate": true}`), and a review
that fails or whose function dies is retried with backoff (2, 4, 8, 16 minutes, 5 attempts) by `retry.yml`, which calls
`POST /internal/pr-gate/retry`. Rows older than 30 days are purged. The one case the queue cannot cover is a delivery that never
reaches the API: the web route retries the forward twice (2 s, 8 s) and then logs/reports the failure; use "Redeliver" in
the GitHub App's Advanced tab to replay it.

The web app sets a Content-Security-Policy (self, Supabase, Sentry; `'unsafe-inline'` for scripts and styles because
Next's bootstrap scripts carry no nonce on static pages), HSTS, `X-Frame-Options: DENY`, `nosniff`, a strict referrer
policy and a minimal Permissions-Policy; the API sends the same set minus the CSP.

## 6. GitHub: Actions secrets

Repo > Settings > Secrets and variables > Actions. Workflows skip with a notice when their secrets are missing.

| Secret | Used by | Value |
|---|---|---|
| `DATABASE_URL` | cluster.yml | Supabase **session** pooler (5432), `postgresql+asyncpg://...` |
| `MONGO_URI`, `MONGO_DB_NAME` (optional) | cluster.yml | as on the API project |
| `REDIS_URL` | cluster.yml | as on the API project (the job takes the same lock the API checks) |
| `BACKUP_PASSPHRASE` | backup.yml | long random string; keep a copy outside GitHub (section 8) |
| `API_BASE_URL` | cron.yml | the API project's URL, no trailing path |
| `INTERNAL_CRON_SECRET` | cron.yml | same value as on the API project |

| Workflow | Schedule (UTC) | Does |
|---|---|---|
| `cron.yml` | daily 02:23 / Mondays 03:41 | `POST /internal/osv-sync` / `/internal/maintainers-sync` with `X-Cron-Secret`; also runnable by hand |
| `cron.yml` (second step) | with every run above | `POST /internal/keepalive`: a query on each of Postgres, Atlas and Redis, `503` naming the one that is down |
| `retry.yml` | every 10 minutes | `POST /internal/pr-gate/retry`, see section 5. Roughly 4,300 minutes a month: free on a public repo, over the 2,000 free minutes on a private one (lower the cron there) |
| `cluster.yml` | daily 03:17 + on dispatch | runs `python -m app.jobs.cluster` (CPU torch, model cached) on a free 4 vCPU runner; one run at a time |
| `audit.yml` | Mondays 04:29 + dependency changes | `pip-audit` on both requirements files and `npm audit --omit=dev --audit-level=high`; fails on findings. Ignore list: `PIP_AUDIT_IGNORE` in the workflow (npm has none: pin a patched transitive version with `overrides` in the root `package.json`) |

`.github/dependabot.yml` opens one grouped weekly PR each for pip, npm, GitHub Actions and Docker/compose images.

## Access model

DephGraph is invite-only. Nobody sees data until an admin puts them in a team.

- **Organisation admins** (`ORG_ADMIN_EMAILS`, comma list, set on the API project; required when
  `ENVIRONMENT=production`, the API refuses to start without it) are the only people who may create
  teams. The first admin signs in, creates a team in Settings, then invites the rest.
- **Invites**: a team admin invites an email address in Settings (valid 7 days, no mail is sent: tell
  the person to sign in at the web URL). The invite is claimed when they sign in with that address.
  Anyone without a team who is not an organisation admin gets "ask an admin for an invite".
- **Confirm email must be ON** (Supabase Dashboard > Authentication > Providers > Email). The API reads
  the address and its confirmation from `auth.users`, never from user-editable metadata; with it OFF
  anyone could sign up as someone else's address and claim their invite.
- **`ALLOWED_EMAIL_DOMAINS`** (optional) additionally restricts sign-in to those domains.
- **Repositories**: registering a service (Services > Add service) requires the DephGraph GitHub App
  to be installed on that repository, so a repository can only be added by someone who controls it.
  A repository (plus directory) can belong to one service only.
- **GitHub sign-in**: use the GitHub App's Client ID and Client secret in Supabase > Authentication >
  Providers > GitHub, with the callback URL Supabase shows there. Email/password stays available.

## Keeping free tiers awake

Supabase pauses a free project after a week without activity and Atlas M0 after 60 days. `cron.yml` runs daily and
its last step calls `/internal/keepalive`, which touches Postgres, Atlas and Redis, so the three stay active as long
as the schedule runs. GitHub disables scheduled workflows after 60 days without a commit on the repository (public or
private); runs of the workflows themselves do not count. Re-enable them in the Actions tab (workflow > Enable
workflow), or push any commit. A failed keep-alive step fails the run, and GitHub emails whoever last edited the schedule.

## Using DephGraph on this repository (dogfooding)

1. Create the GitHub App (the live one is `dephgraph-rohith`, made public so other people can sign in with it
   and install it; webhook URL `https://<web project>/api/webhooks/github`, secret = `GITHUB_WEBHOOK_SECRET`,
   permissions: Actions read and write, Checks read and write, Contents read, Issues read and write, Metadata read,
   Pull requests read, account Email addresses read; events: Pull request, Push, Installation,
   Installation repositories) and install it on this repository. Push and the two installation events import
   a service's manifests; Issues write opens vulnerability alerts; Actions write dispatches `cluster.yml`.
   The live App currently subscribes to Push and Pull request only, so a new installation does not import
   manifests until the next push; add the two installation events if you want that.
2. Register the repository as a service of a team:
   `python scripts/register_service.py --repo https://github.com/Rohith-sp/DephGraph --team <team-slug>`
   (needs `DATABASE_URL`; idempotent; the name defaults to the repository name).
3. Open a PR that adds a dependency: DephGraph posts a Check Run on it. Skipped PRs (repository not registered)
   leave no Check Run; the API log line `pr_gate_skipped` says why.

## 7. Smoke test

```bash
BASE=https://dephgraph-loki-api.vercel.app
curl $BASE/health                         # 200 {"status":"ok"}
curl -i $BASE/me                          # 401 without a token
curl -I https://dephgraph-loki.vercel.app/login   # 200
curl -X POST $BASE/internal/osv-sync -H "X-Cron-Secret: $INTERNAL_CRON_SECRET"
curl $BASE/services -H "Authorization: Bearer $TOKEN"
curl -X POST $BASE/clusters/recompute -H "Authorization: Bearer $TOKEN"   # {"status":"dispatched"}; watch the Actions tab
```

Every response carries an `X-Request-ID` header; the same id is on that request's log lines.
Hobby keeps runtime logs for one hour.

## Self-hosting

`docker compose up -d --build --wait` runs the same code (slim API image, uvicorn). Clustering:
`docker compose --profile ml run --rm cluster`. See the README.

## 8. Backups

Supabase free has no point-in-time recovery. `.github/workflows/backup.yml` runs weekly (Sunday 03:17 UTC, or by hand from the Actions tab) and uploads one encrypted bundle as the artifact `dephgraph-db-backup`, kept 30 days. The bundle holds `public.pgdump` (schema `public`) and `auth.pgdump` (data of `auth.users` and `auth.identities`, so `team_member.user_id` still maps after a restore). It fails loudly if a secret is missing.

Repository secrets:

- `DATABASE_URL`: the same secret `cluster.yml` uses. It must be the Supabase **session pooler** (`*.pooler.supabase.com:5432`). The direct host is IPv6-only and GitHub runners have no IPv6; the transaction pooler (6543) does not work with `pg_dump`. The workflow strips `+asyncpg`.
- `BACKUP_PASSPHRASE`: long random string. Keep a copy outside GitHub: without it the backups are unreadable.

GitHub disables scheduled workflows after 60 days without repository activity. Re-enable under Actions > Backup > "Enable workflow" (any commit or a manual run also resets the clock).

### Restore

```bash
# 1. Download the artifact from the workflow run page and unzip it
# 2. Decrypt and unpack (passphrase via stdin, never argv)
printf '%s' "$BACKUP_PASSPHRASE" | gpg --batch --pinentry-mode loopback --passphrase-fd 0 \
  -d -o bundle.tar dephgraph-YYYY-MM-DD.tar.gpg
tar -xf bundle.tar                       # public.pgdump, auth.pgdump (empty if auth was absent)

# 3. Restore into a FRESH Supabase project (it already has the auth schema, the `authenticated`
#    role and auth.uid()). Use its session pooler URL. Client major >= the dump's (17).
pg_restore --no-owner --no-acl --data-only -d "$NEW_URL" auth.pgdump      # auth first
pg_restore --no-owner --no-acl -d "$NEW_URL" public.pgdump                # then public
```

`pg_restore` exits non-zero on any error: read the error list instead of trusting the exit code alone (`pg_restore -l` lists contents). Two errors are harmless: `schema "public" already exists`, and (restoring into a server older than 17) `unrecognized configuration parameter "transaction_timeout"`.

Plain Postgres instead of Supabase: before step 3 create the pieces Supabase normally provides, as migration 010 does:

```sql
CREATE ROLE authenticated NOLOGIN;
CREATE SCHEMA auth;
CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS $f$
  SELECT coalesce(nullif(current_setting('request.jwt.claim.sub', true), ''),
                  nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')::uuid $f$;
```

and skip `auth.pgdump` (there is no `auth.users` to load it into; the user ids in `team_member` stay as plain UUIDs). MongoDB and Redis are not covered: Mongo holds raw scan data that can be re-ingested, and Redis is cache only.

Collectors that report runtime usage are in [collectors/README.md](../collectors/README.md).
