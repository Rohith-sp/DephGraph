# Deploying DephGraph

Order: Supabase, then Render (API), then Vercel (web), then cron-job.org.
Every variable below is documented in [`.env.example`](../.env.example).

## 1. Supabase (Postgres + auth)

Apply the migrations in numeric order, each once, in the SQL editor or with `psql -v ON_ERROR_STOP=1 -f`:

```
001_initial_schema.sql  ->  002_views.sql  ->  003_indexes.sql  ->  004_rls.sql  ->  005_cluster_keywords.sql  ->  006_view_fixes.sql
```

Existing migrations are immutable; schema changes go in a new `007_*.sql`.

**RLS is single-tenant.** `004_rls.sql` enables RLS on every table with
`FOR SELECT TO authenticated USING (true)`: any signed-in user can read all data. All writes go
through the API, which connects as the table owner (bypasses RLS). Before going live either:

- disable open sign-up (Auth > Providers > Email > "Allow new users to sign up" off, invite users instead), or
- add team scoping: the schema has no user-to-team link yet, so add one in a new migration, replace
  `USING (true)` with a predicate on it, and enforce the same check in the API.

Use the pooled connection string for `DATABASE_URL`, with the `postgresql+asyncpg://` scheme.

## 2. Render (API)

New > Blueprint, point it at this repo; it reads `apps/api/render.yaml` (one web service, `--workers 1`,
health check `/health`). Set the `sync: false` variables in the dashboard:

| Variable | Value |
|---|---|
| `DATABASE_URL` | Supabase Postgres, `postgresql+asyncpg://...` |
| `MONGO_URI` | MongoDB Atlas SRV string |
| `REDIS_URL` | Upstash `rediss://...` |
| `SUPABASE_JWT_SECRET` | Supabase > Project Settings > API > JWT secret |
| `GROQ_API_KEY` | Groq key (PR gate stage 3) |
| `GITHUB_APP_ID`, `GITHUB_PRIVATE_KEY_BASE64`, `GITHUB_WEBHOOK_SECRET` | GitHub App; key is the PEM, base64-encoded |
| `INTERNAL_CRON_SECRET` | long random string; guards `/internal/*` |
| `CORS_ORIGINS` | JSON list, e.g. `["https://dephgraph.vercel.app"]` |

`ENVIRONMENT=production` (JSON logs, SBERT preload) and `TRUSTED_PROXY_HOPS=1` are preset in the blueprint.
Optional: `MONGO_DB_NAME`, `GROQ_MODEL`, `RATE_LIMIT` (default `100/minute`).

Rate limiting is per client IP, in memory, so it needs a single worker. The client IP is the
`X-Forwarded-For` entry added by the last trusted proxy (`TRUSTED_PROXY_HOPS` entries from the right);
entries further left are client-supplied and ignored. If you put another proxy/CDN in front of Render, raise
`TRUSTED_PROXY_HOPS` to match, otherwise all clients share one bucket.

## 3. Vercel (web)

Import the repo, Root Directory `apps/web`. Environment variables:

| Variable | Value |
|---|---|
| `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase project URL and anon key |
| `NEXT_PUBLIC_API_URL` | Render URL, e.g. `https://dephgraph-api.onrender.com` |
| `API_URL` | optional server-side override of the API base |
| `GITHUB_WEBHOOK_SECRET` | same value as on Render; the web app verifies GitHub signatures and forwards to the API |

**`AUTH_DISABLED` must be unset or `false` in production.** `true` skips the login middleware and sends
`DEV_API_TOKEN` to the API. Leave `DEV_API_TOKEN` unset in production too. Never expose
`SUPABASE_SERVICE_ROLE_KEY` to the browser or set it on Vercel; the app does not need it.

## 4. cron-job.org

Send the secret in a request header (Advanced > Headers). Times are in the job's timezone (Asia/Kolkata).

| Job | Method + URL | Header | Schedule |
|---|---|---|---|
| OSV sync | `POST https://dephgraph-api.onrender.com/internal/osv-sync` | `X-Cron-Secret: <INTERNAL_CRON_SECRET>` | `0 2 * * *` |
| Cluster recompute | `POST https://dephgraph-api.onrender.com/clusters/recompute` | `Authorization: Bearer <JWT>` (see note) | `0 3 * * 0` |
| Health ping | `GET https://dephgraph-api.onrender.com/health` | none | `*/10 * * * *` |

Note: `/clusters/recompute` currently requires a user JWT, not `X-Cron-Secret`, and Supabase access tokens
expire, so this job will start failing with 401 after the token lapses. Until the endpoint accepts the cron
secret, treat this job as manual or refresh the token.

`/health` is exempt from rate limiting; other routes return `429` with the standard envelope after 100 requests
per minute per IP.

## 5. Smoke test

```bash
BASE=https://dephgraph-api.onrender.com
curl $BASE/health
curl -X POST $BASE/internal/osv-sync -H "X-Cron-Secret: $INTERNAL_CRON_SECRET"
curl $BASE/blast-radius/1 -H "Authorization: Bearer $TOKEN"
```

Every response carries an `X-Request-ID` header; the same id is on that request's log lines.
