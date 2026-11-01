# DephGraph — Application Build PRD
## Software Supply Chain Risk Intelligence Platform

**Version**: 1.0.0  
**Date**: November 1, 2026  
**Author**: Rakshak S (RVCE, 4th Sem CSE)  
**Status**: Ready to Build  
**Short name**: DephGraph  

---

## 0. The One-Liner

> *"Every tool tells you which packages have vulnerabilities. DephGraph tells you which you can delete, which are redundant, and which single upgrade fixes the most services — org-wide."*

---

## 1. Product Vision & Goals

### What We're Building
A full-stack web application that:
- Ingests dependency manifests (`requirements.txt`, `package.json`, `Cargo.toml`, `go.mod`) from multiple services/repos
- Stores the complete dependency graph in PostgreSQL with proper relational integrity (23 tables)
- Fetches real vulnerability data from OSV.dev API (free, no auth) and deps.dev API (free)
- Computes: blast radius, minimum remediation set (set-cover), liveness evidence, license propagation, semantic clustering
- Displays all intelligence in a React dashboard with interactive dependency graph
- Gates PRs via GitHub Checks API using a 3-stage filter (manifest check → SQL verdict → LLM advisory)

### Success Criteria by Phase

| Phase | Definition of Done |
|-------|-------------------|
| 0 — Setup | Repo live, all cloud accounts connected, local dev boots, CI green |
| 1 — Schema | All 23 tables in Supabase, FK constraints valid, seed data queryable |
| 2 — Ingestion | Upload lockfile → packages in DB → OSV vulns fetched within 60s |
| 3 — Analysis | All 5 signature queries return correct results on test data |
| 4 — Dashboard | All 5 pages load <3s, dep graph renders 500+ nodes without crashing |
| 5 — Clustering | 100 packages → ≥3 clusters with human-readable labels in UI |
| 6 — PR Gate | Mock PR webhook → verdict stored → GitHub Check created within 30s |
| 7 — Production | E2E: lockfile to risk verdict <30s, deployed on live free-tier URLs |

---

## 2. Tech Stack

| Layer | Technology | Version | Why | Free? |
|-------|-----------|---------|-----|-------|
| Frontend framework | Next.js (App Router) | 15.0.3 | SSR, file-based routing, Vercel native | ✅ |
| Language (FE) | TypeScript | 5.6.3 | Type safety, Supabase type-gen | ✅ |
| Styling | Tailwind CSS + shadcn/ui | 3.4.14 | Best DX for dashboards, accessible primitives | ✅ |
| Graph visualization | ReactFlow | 11.11.4 | Handles 500+ nodes, interactive, customizable | ✅ |
| Charts | Recharts | 2.13.3 | Lightweight, composable, works with RSC | ✅ |
| Backend API | FastAPI (Python) | 0.115.0 | Async native, auto-docs, great ecosystem | ✅ |
| ASGI server | Uvicorn | 0.32.0 | Production-grade ASGI | ✅ |
| ORM | SQLAlchemy (async) | 2.0.36 | Best PostgreSQL ORM, async-first in v2 | ✅ |
| Validation | Pydantic v2 | 2.9.2 | Fast, FastAPI native, strict types | ✅ |
| Primary DB | **Supabase (PostgreSQL 15)** | — | 500MB free, Auth, RLS, PostgREST, Edge Fns | ✅ Free |
| Document store | **MongoDB Atlas M0** | — | 512MB free, no CC required | ✅ Free |
| Cache | **Upstash Redis** | — | 10K commands/day free, serverless Redis | ✅ Free |
| Auth | Supabase Auth | — | JWT, OAuth, magic link, built-in | ✅ Free |
| HTTP client | HTTPX | 0.27.2 | Async HTTP for OSV.dev + deps.dev calls | ✅ |
| Clustering — embeddings | sentence-transformers | 3.3.1 | SBERT `all-MiniLM-L6-v2`, 80MB, fast | ✅ |
| Clustering — dim reduction | umap-learn | 0.5.7 | UMAP 2D for scatter viz | ✅ |
| Clustering — density | hdbscan | 0.8.39 | Noise-tolerant, GROUPED_INTO justification | ✅ |
| Stage 3 LLM | Groq API | — | Free tier: 30 req/min, LLaMA 3.1 70B | ✅ Free |
| Frontend deploy | **Vercel** | — | Free tier, Next.js native, global CDN | ✅ Free |
| Backend deploy | **Render** | — | Free web service (sleeps after 15min) | ✅ Free |
| Cron scheduler | **cron-job.org** | — | Free HTTP cron, up to 50 jobs | ✅ Free |
| CI/CD | GitHub Actions | — | Free for public repos | ✅ Free |
| Monorepo | Turborepo | 2.3.0 | Caches build artifacts, parallel task runner | ✅ |

---

## 3. Architecture

### 3.1 High-Level System Architecture

```mermaid
flowchart TB
    subgraph INGESTION["📥 Ingestion Layer"]
        A[GitHub Webhook\nPR opened] --> C[FastAPI\nIngestion API]
        B[Manual Upload\nvia Dashboard] --> C
        D[CLI Scanner\nrun on local repo] --> C
        E[OSV.dev API\nFree · No Auth] --> C
        F[deps.dev API\nFree · No Auth] --> C
    end

    subgraph COMPUTE["⚙️ Compute Layer — FastAPI on Render"]
        C --> G[Manifest Parser\nreq.txt / pkg.json / Cargo.toml / go.mod]
        G --> H{Staging Tables\nValidate & Deduplicate}
        H -->|Valid| I[Atomic Commit\nto PostgreSQL]
        H -->|Invalid| J[Error Response\nto client]
        I --> K[OSV Sync\nBackground Worker]
        K --> L[Vuln Hydration\nin PostgreSQL]
    end

    subgraph STORAGE["🗄️ Storage Layer"]
        L --> M[(Supabase\nPostgreSQL 15\n23 tables · 500MB free)]
        K --> N[(MongoDB Atlas\nM0 Cluster\nraw docs · embeddings · 512MB free)]
        O[(Upstash Redis\nCTE cache · 24h TTL\n10K cmds/day free)]
    end

    subgraph ANALYSIS["🔍 Analysis Engine"]
        M --> P[Blast Radius\nRecursive CTE]
        M --> Q[Set-Cover\nGreedy Min Remediation]
        M --> R[License Propagation\nclosure query]
        M --> S[Prune Anti-join\nzero-exec packages]
        N --> T[SBERT + UMAP\n+ HDBSCAN\nClustering Engine]
        T --> M
        P --> O
        Q --> O
    end

    subgraph PRGATE["🚦 PR Gate — 3-Stage Filter"]
        A --> U[Stage 1\nManifest Check\n~40% exit here · PASS]
        U --> V[Stage 2\nSQL Verdict\n~18% exit here · PASS or BLOCK]
        V --> W[Stage 3\nLLM via Groq\n~42% reach here · Advisory]
        W --> X[GitHub Checks API\nPass / Advisory / Block]
    end

    subgraph FRONTEND["🖥️ Frontend — Next.js on Vercel"]
        Y[Dashboard\n5 pages] --> M
        Y --> N
        Y --> O
    end
```

### 3.2 Ingestion Pipeline Data Flow

```mermaid
flowchart LR
    A[📄 Upload\nrequirements.txt] --> B[Parse\nextract pkg + version]
    B --> C{In DB\nalready?}
    C -->|No| D[Insert\npackage_version\nstaging]
    C -->|Yes| E[Version bump\ncheck only]
    D --> F[deps.dev API\nfetch metadata\nlicense · published_at]
    F --> G[OSV.dev API\nfetch advisories\nCVEs · GHSA IDs]
    G --> H{Any\nvulns?}
    H -->|Yes| I[Insert vulnerability\n+ affects records]
    H -->|No| J[Mark clean\nscan_run]
    I --> K[Atomic commit\nall 23 tables]
    J --> K
    K --> L[Invalidate\nRedis keys\nfor this service]
    L --> M[Webhook callback\nSSE to dashboard]
    E --> G
```

### 3.3 PR Gate — 3-Stage Filter

```mermaid
flowchart TD
    A[GitHub PR Webhook\nnew dependency added] --> B{Stage 1\nManifest Check}
    B -->|No package in affects table| C[✅ PASS\nNo vulns · fast exit\n~400/1000 PRs]
    B -->|Package found in affects| D{Stage 2\nSQL Verdict}
    D -->|CVSS < 6.0\nabandonment < 40\nno copyleft| E[✅ PASS\nLow risk · SQL cleared\n~180/1000 PRs]
    D -->|CVSS ≥ 9.0\nOR copyleft license| F[🔴 BLOCK\nAuto-block · no LLM needed]
    D -->|Medium risk\nneeds context| G{Stage 3\nLLM via Groq\nLLaMA 3.1 70B}
    G --> H[ADVISORY or BLOCK\nbased on full context\n~420/1000 PRs]
    C --> I[GitHub Checks API\nwrite verdict]
    E --> I
    F --> I
    H --> I
    I --> J[Store in\npr_risk_verdict table]
```

### 3.4 Free Deployment Architecture

```mermaid
flowchart TB
    subgraph USERS["👤 Users"]
        A[Browser]
    end

    subgraph VERCEL["▲ Vercel — Free Tier"]
        B[Next.js 15\nSSR + Static]
        C[Route: /api/webhooks/github\nValidates signature · forwards]
    end

    subgraph RENDER["🎯 Render — Free Tier\n750h/month · sleeps 15min"]
        D[FastAPI\nWeb Service :8000]
        E[OSV Sync Worker\nBackground task]
    end

    subgraph DATABASES["☁️ Cloud DBs — All Free Forever"]
        F[(Supabase\nPostgreSQL\n500MB)]
        G[(MongoDB Atlas\nM0 · 512MB)]
        H[(Upstash\nRedis · 10K/day)]
    end

    subgraph EXTERNAL_APIS["🌐 External APIs — All Free"]
        I[OSV.dev\nNo auth]
        J[deps.dev\nNo auth]
        K[GitHub API\nOAuth App]
        L[Groq API\nFree tier\nLLaMA 3.1 70B]
    end

    subgraph CRON["⏰ cron-job.org — Free"]
        M[OSV sync daily\n0 2 * * *]
        N[Cluster weekly\n0 3 * * 0]
        O[Health ping\nevery 10min\nkeeps Render warm]
    end

    A --> VERCEL
    VERCEL --> RENDER
    RENDER --> DATABASES
    RENDER --> EXTERNAL_APIS
    CRON --> D
```

### 3.5 ER Entity Relationship Overview

```mermaid
erDiagram
    TEAM ||--o{ SERVICE : OWNS
    TEAM ||--o{ REMEDIATION_PLAN : RAISED_BY
    SERVICE ||--o{ DEPLOYMENT : DEPLOYED_AS
    SERVICE ||--o{ PR_REVIEW : TARGETS
    SERVICE ||--o{ SCAN_RUN : runs
    PACKAGE ||--o{ PACKAGE_VERSION : HAS_VERSION
    PACKAGE }o--o{ MAINTAINER : MAINTAINED_BY
    PACKAGE_VERSION }o--o{ VULNERABILITY : AFFECTS
    PACKAGE_VERSION }o--o{ PACKAGE_VERSION : DEPENDS_ON
    PACKAGE_VERSION }o--o| LICENSE : LICENSED_UNDER
    PACKAGE_VERSION }o--o| SEMANTIC_CLUSTER : GROUPED_INTO
    DEPLOYMENT }o--o{ PACKAGE_VERSION : RESOLVES_TO
    REMEDIATION_PLAN ||--o{ REMEDIATION_ACTION : CONTAINS
    REMEDIATION_ACTION }o--o{ VULNERABILITY : RESOLVES
    VULNERABILITY }o--o{ SERVICE : WAIVED_FOR
    PR_REVIEW ||--o{ PR_RISK_VERDICT : PRODUCED_FOR
```

---

## 4. Free Deployment — Detailed Setup

### 4.1 Supabase (Primary Database)
- **Free tier**: 500MB storage, 2 active projects, unlimited API requests, weekly backups
- **What we use**: PostgreSQL 15, Row Level Security, PostgREST, Auth (JWT + OAuth), Edge Functions
- **Setup**: supabase.com → New project → SQL Editor → paste each migration → Run
- **Connection**: `postgresql+asyncpg://postgres:[pw]@db.[ref].supabase.co:5432/postgres`

### 4.2 MongoDB Atlas M0
- **Free tier**: 512MB, 3-node replica set, no credit card, free forever
- **Collections**: `raw_manifests`, `osv_advisories`, `package_embeddings`, `runtime_samples`, `review_responses`
- **Setup**: cloud.mongodb.com → Create free M0 cluster (AWS ap-south-1 for India latency)

### 4.3 Upstash Redis
- **Free tier**: 10,000 commands/day, 256MB max, no credit card
- **Stores**: memoized blast-radius CTE subtrees (24h TTL), Stage 3 prompt cache (7d TTL)
- **Key pattern**: `dephgraph:{resource}:{id}` e.g. `dephgraph:blast_radius:service_3`
- **Setup**: console.upstash.com → Create database (ap-south-1) → Copy REST URL + token

### 4.4 Vercel (Frontend)
- **Free tier**: Unlimited deployments, 100GB bandwidth/month, custom domain, Serverless Functions
- **Setup**: vercel.com → Import GitHub repo → Root Directory = `apps/web` → Deploy

### 4.5 Render (Backend API)
- **Free tier**: 750 hours/month, auto-deploys from GitHub, custom domain
- **Limitation**: Sleeps after 15min inactivity, ~30s cold start
- **Workaround**: cron-job.org pings `/health` every 10min to keep it warm
- **Setup**: render.com → New Web Service → connect repo → root = `apps/api`

### 4.6 Groq API (Stage 3 LLM)
- **Free tier**: 30 requests/minute, 6,000 tokens/minute, LLaMA 3.1 70B + Mixtral 8x7B
- **Why enough**: Only ~420/1000 PRs reach Stage 3. Most orgs have <10 PRs/day.
- **Fallback**: `ollama run deepseek-coder-v2` locally during development

### 4.7 cron-job.org
- **Free**: 50 cron jobs, minimum 1-minute intervals, email alerts on failure

| Job | URL | Schedule | Purpose |
|-----|-----|----------|---------|
| OSV Sync | `POST /internal/osv-sync` | `0 2 * * *` | Daily vuln refresh at 2 AM IST |
| Cluster Recompute | `POST /clusters/recompute` | `0 3 * * 0` | Weekly Sunday 3 AM IST |
| Health Ping | `GET /health` | `*/10 * * * *` | Keep Render warm |

---

## 5. Database Schema (PostgreSQL — Supabase)

> 23 total tables. All timestamps are `TIMESTAMPTZ`. All IDs are `BIGSERIAL`.
> Mapping rule: 1:N → FK only (no new table). M:N → own table. Ternary → own table. Multivalued → own table.

### 5.1 Migration 001 — Entity Tables (14)

```sql
-- ╔══════════════════════════════════════╗
-- ║   STRONG ENTITIES (9)                ║
-- ╚══════════════════════════════════════╝

CREATE TABLE team (
  team_id    BIGSERIAL    PRIMARY KEY,
  name       VARCHAR(120) NOT NULL UNIQUE,
  slug       VARCHAR(80)  NOT NULL UNIQUE,
  created_at TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE service (
  service_id BIGSERIAL    PRIMARY KEY,
  team_id    BIGINT       NOT NULL REFERENCES team(team_id) ON DELETE CASCADE,
  name       VARCHAR(120) NOT NULL,
  repo_url   VARCHAR(500),
  language   VARCHAR(40),
  created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
  UNIQUE(team_id, name)
);

CREATE TABLE package (
  package_id  BIGSERIAL    PRIMARY KEY,
  ecosystem   VARCHAR(40)  NOT NULL CHECK (ecosystem IN ('npm','pypi','cargo','go','maven','nuget')),
  name        VARCHAR(255) NOT NULL,
  description TEXT,
  created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
  UNIQUE(ecosystem, name)
);

CREATE TABLE maintainer (
  maintainer_id BIGSERIAL    PRIMARY KEY,
  username      VARCHAR(120) NOT NULL,
  platform      VARCHAR(40)  NOT NULL DEFAULT 'github',
  profile_url   VARCHAR(500),
  UNIQUE(platform, username)
);

CREATE TABLE semantic_cluster (
  cluster_id   BIGSERIAL    PRIMARY KEY,
  label        VARCHAR(120) NOT NULL,
  centroid_vec JSONB,
  member_count INT          NOT NULL DEFAULT 0,
  created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE vulnerability (
  vuln_id      BIGSERIAL    PRIMARY KEY,
  osv_id       VARCHAR(40)  NOT NULL UNIQUE,
  severity     VARCHAR(20)  CHECK (severity IN ('CRITICAL','HIGH','MEDIUM','LOW','NONE')),
  cvss_score   NUMERIC(4,2),
  title        VARCHAR(500),
  published_at TIMESTAMPTZ,
  modified_at  TIMESTAMPTZ
);

CREATE TABLE license (
  license_id  BIGSERIAL   PRIMARY KEY,
  spdx_id     VARCHAR(60) NOT NULL UNIQUE,
  name        VARCHAR(200),
  is_copyleft BOOLEAN     NOT NULL DEFAULT FALSE
);

CREATE TABLE remediation_plan (
  plan_id      BIGSERIAL    PRIMARY KEY,
  team_id      BIGINT       NOT NULL REFERENCES team(team_id) ON DELETE CASCADE,
  title        VARCHAR(255),
  coverage_pct NUMERIC(5,2) DEFAULT 0,
  created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
  status       VARCHAR(20)  NOT NULL DEFAULT 'DRAFT'
               CHECK (status IN ('DRAFT','ACTIVE','CLOSED'))
);

CREATE TABLE scan_run (
  scan_id      BIGSERIAL   PRIMARY KEY,
  service_id   BIGINT      NOT NULL REFERENCES service(service_id) ON DELETE CASCADE,
  started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ,
  source       VARCHAR(20) NOT NULL DEFAULT 'manual'
               CHECK (source IN ('manual','pr_hook','scheduled','cli'))
);

-- ╔══════════════════════════════════════╗
-- ║   WEAK ENTITIES (5)                  ║
-- ╚══════════════════════════════════════╝

-- DEPLOYMENT: weak, discriminator = deploy_seq within service_id
CREATE TABLE deployment (
  service_id   BIGINT      NOT NULL REFERENCES service(service_id) ON DELETE CASCADE,
  deploy_seq   INT         NOT NULL,
  environment  VARCHAR(40) NOT NULL DEFAULT 'production',
  deployed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (service_id, deploy_seq)
);

-- PACKAGE_VERSION: weak, discriminator = version_string within package_id
CREATE TABLE package_version (
  package_id     BIGINT       NOT NULL REFERENCES package(package_id) ON DELETE CASCADE,
  version_string VARCHAR(100) NOT NULL,
  license_id     BIGINT       REFERENCES license(license_id) ON DELETE SET NULL,
  cluster_id     BIGINT       REFERENCES semantic_cluster(cluster_id) ON DELETE SET NULL,
  published_at   TIMESTAMPTZ,
  is_yanked      BOOLEAN      NOT NULL DEFAULT FALSE,
  PRIMARY KEY (package_id, version_string)
);

-- REMEDIATION_ACTION: weak, discriminator = action_seq within plan_id
CREATE TABLE remediation_action (
  plan_id     BIGINT      NOT NULL REFERENCES remediation_plan(plan_id) ON DELETE CASCADE,
  action_seq  INT         NOT NULL,
  description TEXT,
  status      VARCHAR(20) NOT NULL DEFAULT 'PENDING'
              CHECK (status IN ('PENDING','DONE','SKIPPED')),
  PRIMARY KEY (plan_id, action_seq)
);

-- PR_REVIEW: weak, discriminator = pr_number within service_id
CREATE TABLE pr_review (
  service_id BIGINT       NOT NULL REFERENCES service(service_id) ON DELETE CASCADE,
  pr_number  INT          NOT NULL,
  pr_url     VARCHAR(500),
  author     VARCHAR(120),
  opened_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
  PRIMARY KEY (service_id, pr_number)
);

-- PR_RISK_VERDICT: weak entity of PR_REVIEW, discriminator = dep_name within PR
CREATE TABLE pr_risk_verdict (
  service_id    BIGINT       NOT NULL,
  pr_number     INT          NOT NULL,
  dep_name      VARCHAR(255) NOT NULL,
  risk_level    VARCHAR(20)  NOT NULL CHECK (risk_level IN ('PASS','ADVISORY','BLOCK')),
  stage_reached INT          NOT NULL CHECK (stage_reached BETWEEN 1 AND 3),
  context_tokens INT,
  decided_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
  PRIMARY KEY (service_id, pr_number, dep_name),
  FOREIGN KEY (service_id, pr_number)
    REFERENCES pr_review(service_id, pr_number) ON DELETE CASCADE
);
```

### 5.2 Migration 001 — Relationship Tables (9)

```sql
-- ╔══════════════════════════════════════╗
-- ║   M:N RELATIONSHIP TABLES (6)        ║
-- ╚══════════════════════════════════════╝

-- MAINTAINED_BY (PACKAGE ↔ MAINTAINER)
CREATE TABLE maintained_by (
  package_id    BIGINT NOT NULL REFERENCES package(package_id) ON DELETE CASCADE,
  maintainer_id BIGINT NOT NULL REFERENCES maintainer(maintainer_id) ON DELETE CASCADE,
  role          VARCHAR(40) NOT NULL DEFAULT 'owner',
  PRIMARY KEY (package_id, maintainer_id)
);

-- RESOLVES_TO — materialized transitive closure (DEPLOYMENT ↔ PACKAGE_VERSION)
-- NOT computed on-the-fly; maintained by background job (cites Agrawal/Borgida 1989)
CREATE TABLE resolves_to (
  service_id     BIGINT       NOT NULL,
  deploy_seq     INT          NOT NULL,
  package_id     BIGINT       NOT NULL,
  version_string VARCHAR(100) NOT NULL,
  depth          INT          NOT NULL DEFAULT 0,
  is_direct      BOOLEAN      NOT NULL DEFAULT FALSE,
  PRIMARY KEY (service_id, deploy_seq, package_id, version_string),
  FOREIGN KEY (service_id, deploy_seq)
    REFERENCES deployment(service_id, deploy_seq) ON DELETE CASCADE,
  FOREIGN KEY (package_id, version_string)
    REFERENCES package_version(package_id, version_string) ON DELETE CASCADE
);

-- DEPENDS_ON — recursive M:N self-join on PACKAGE_VERSION
-- roles: "dependent" (left) and "dependency" (right)
CREATE TABLE depends_on (
  dep_package_id     BIGINT       NOT NULL,
  dep_version_string VARCHAR(100) NOT NULL,
  ref_package_id     BIGINT       NOT NULL,
  ref_version_string VARCHAR(100) NOT NULL,
  scope              VARCHAR(20)  CHECK (scope IN ('runtime','dev','optional','peer')),
  PRIMARY KEY (dep_package_id, dep_version_string, ref_package_id, ref_version_string),
  FOREIGN KEY (dep_package_id, dep_version_string)
    REFERENCES package_version(package_id, version_string) ON DELETE CASCADE,
  FOREIGN KEY (ref_package_id, ref_version_string)
    REFERENCES package_version(package_id, version_string) ON DELETE CASCADE
);

-- AFFECTS (PACKAGE_VERSION ↔ VULNERABILITY)
CREATE TABLE affects (
  package_id     BIGINT       NOT NULL,
  version_string VARCHAR(100) NOT NULL,
  vuln_id        BIGINT       NOT NULL REFERENCES vulnerability(vuln_id) ON DELETE CASCADE,
  fixed_in       VARCHAR(100),
  PRIMARY KEY (package_id, version_string, vuln_id),
  FOREIGN KEY (package_id, version_string)
    REFERENCES package_version(package_id, version_string) ON DELETE CASCADE
);

-- RESOLVES (REMEDIATION_ACTION ↔ VULNERABILITY)
CREATE TABLE resolves (
  plan_id    BIGINT NOT NULL,
  action_seq INT    NOT NULL,
  vuln_id    BIGINT NOT NULL REFERENCES vulnerability(vuln_id) ON DELETE CASCADE,
  PRIMARY KEY (plan_id, action_seq, vuln_id),
  FOREIGN KEY (plan_id, action_seq)
    REFERENCES remediation_action(plan_id, action_seq) ON DELETE CASCADE
);

-- WAIVED_FOR (VULNERABILITY ↔ SERVICE) — accepted risk waivers
CREATE TABLE waived_for (
  vuln_id    BIGINT NOT NULL REFERENCES vulnerability(vuln_id) ON DELETE CASCADE,
  service_id BIGINT NOT NULL REFERENCES service(service_id) ON DELETE CASCADE,
  reason     TEXT,
  expires_on DATE,
  PRIMARY KEY (vuln_id, service_id)
);

-- ╔══════════════════════════════════════╗
-- ║   TERNARY RELATIONSHIP TABLE (1)     ║
-- ╚══════════════════════════════════════╝

-- OBSERVES (DEPLOYMENT × SCAN_RUN × PACKAGE_VERSION)
-- Ternary because: "exec_count=0 during THIS scan window for THIS deployment" is the full fact.
-- Decomposing to binary loses the observation window — "zero calls in which window?" is undefined.
CREATE TABLE observes (
  service_id     BIGINT       NOT NULL,
  deploy_seq     INT          NOT NULL,
  scan_id        BIGINT       NOT NULL REFERENCES scan_run(scan_id) ON DELETE CASCADE,
  package_id     BIGINT       NOT NULL,
  version_string VARCHAR(100) NOT NULL,
  exec_count     INT          NOT NULL DEFAULT 0,
  is_live        BOOLEAN      NOT NULL DEFAULT FALSE,
  PRIMARY KEY (service_id, deploy_seq, scan_id, package_id, version_string),
  FOREIGN KEY (service_id, deploy_seq)
    REFERENCES deployment(service_id, deploy_seq) ON DELETE CASCADE,
  FOREIGN KEY (package_id, version_string)
    REFERENCES package_version(package_id, version_string) ON DELETE CASCADE
);

-- ╔══════════════════════════════════════╗
-- ║   MULTIVALUED ATTRIBUTE TABLES (2)   ║
-- ╚══════════════════════════════════════╝

CREATE TABLE package_alias (
  package_id BIGINT       NOT NULL REFERENCES package(package_id) ON DELETE CASCADE,
  alias      VARCHAR(255) NOT NULL,
  PRIMARY KEY (package_id, alias)
);

CREATE TABLE maintainer_email (
  maintainer_id BIGINT       NOT NULL REFERENCES maintainer(maintainer_id) ON DELETE CASCADE,
  email         VARCHAR(255) NOT NULL,
  is_primary    BOOLEAN      NOT NULL DEFAULT FALSE,
  PRIMARY KEY (maintainer_id, email)
);
```

### 5.3 Migration 002 — Views (5 Signature Queries)

```sql
-- 1. BLAST RADIUS: transitive vulnerability exposure via recursive CTE
CREATE OR REPLACE VIEW v_blast_radius AS
WITH RECURSIVE closure AS (
  SELECT package_id AS pkg_id, version_string AS ver, 0 AS depth
  FROM package_version
  WHERE (package_id, version_string) IN (SELECT package_id, version_string FROM affects)
  UNION ALL
  SELECT d.dep_package_id, d.dep_version_string, c.depth + 1
  FROM depends_on d
  JOIN closure c ON c.pkg_id = d.ref_package_id AND c.ver = d.ref_version_string
  WHERE c.depth < 10
)
SELECT DISTINCT pkg_id, ver, depth FROM closure;

-- 2. PRUNABLE PACKAGES: in resolves_to but never observed alive
CREATE OR REPLACE VIEW v_prunable AS
SELECT
  p.name              AS package_name,
  pv.version_string,
  s.name              AS service_name,
  s.service_id,
  rt.depth            AS dependency_depth
FROM package p
JOIN package_version pv ON p.package_id = pv.package_id
JOIN resolves_to rt     ON (rt.package_id, rt.version_string) = (pv.package_id, pv.version_string)
JOIN deployment d       ON (rt.service_id, rt.deploy_seq) = (d.service_id, d.deploy_seq)
JOIN service s          ON d.service_id = s.service_id
WHERE NOT EXISTS (
  SELECT 1 FROM observes o
  WHERE o.package_id     = pv.package_id
    AND o.version_string = pv.version_string
    AND o.is_live        = TRUE
);

-- 3. ABANDONMENT SCORE: 0-100 where HIGHER = MORE abandoned = WORSE
CREATE OR REPLACE VIEW v_abandonment AS
SELECT
  p.name,
  pv.version_string,
  p.ecosystem,
  LEAST(100, GREATEST(0,
    COALESCE(EXTRACT(YEAR FROM AGE(now(), pv.published_at)) * 15, 50)
    + CASE WHEN pv.is_yanked THEN 40 ELSE 0 END
    - (SELECT COUNT(*) FROM maintained_by mb WHERE mb.package_id = p.package_id) * 8
  ))::INT AS abandonment_score
FROM package p
JOIN package_version pv ON p.package_id = pv.package_id;

-- 4. LICENSE PROPAGATION: copyleft contamination via transitive closure
CREATE OR REPLACE VIEW v_copyleft_risk AS
SELECT DISTINCT
  rt.service_id,
  s.name  AS service_name,
  p.name  AS package_name,
  pv.version_string,
  l.spdx_id,
  rt.depth
FROM resolves_to rt
JOIN package_version pv ON (rt.package_id, rt.version_string) = (pv.package_id, pv.version_string)
JOIN package p          ON pv.package_id = p.package_id
JOIN license l          ON pv.license_id = l.license_id
JOIN service s          ON rt.service_id = s.service_id
WHERE l.is_copyleft = TRUE;

-- 5. CLUSTER REDUNDANCY: multiple packages same function in same service
CREATE OR REPLACE VIEW v_cluster_redundancy AS
SELECT
  sc.label            AS cluster_label,
  s.name              AS service_name,
  COUNT(DISTINCT p.package_id) AS redundant_package_count,
  ARRAY_AGG(DISTINCT p.name ORDER BY p.name) AS packages
FROM semantic_cluster sc
JOIN package_version pv ON pv.cluster_id = sc.cluster_id
JOIN package p          ON pv.package_id = p.package_id
JOIN resolves_to rt     ON (rt.package_id, rt.version_string) = (pv.package_id, pv.version_string)
JOIN service s          ON rt.service_id = s.service_id
GROUP BY sc.cluster_id, sc.label, s.service_id, s.name
HAVING COUNT(DISTINCT p.package_id) > 1;
```

### 5.4 Migration 003 — Performance Indexes

```sql
-- FK indexes (PostgreSQL does NOT auto-index FKs)
CREATE INDEX idx_service_team_id         ON service(team_id);
CREATE INDEX idx_scan_run_service_id     ON scan_run(service_id);
CREATE INDEX idx_package_version_license ON package_version(license_id);
CREATE INDEX idx_package_version_cluster ON package_version(cluster_id);
CREATE INDEX idx_resolves_to_service     ON resolves_to(service_id, deploy_seq);
CREATE INDEX idx_resolves_to_pkg_ver     ON resolves_to(package_id, version_string);
CREATE INDEX idx_depends_on_ref          ON depends_on(ref_package_id, ref_version_string);
CREATE INDEX idx_affects_vuln_id         ON affects(vuln_id);
CREATE INDEX idx_observes_live           ON observes(service_id, deploy_seq) WHERE is_live = TRUE;
CREATE INDEX idx_waived_for_service      ON waived_for(service_id);

-- Query-pattern indexes
CREATE INDEX idx_vulnerability_severity  ON vulnerability(severity);
CREATE INDEX idx_vulnerability_cvss      ON vulnerability(cvss_score DESC);
CREATE INDEX idx_package_ecosystem_name  ON package(ecosystem, name);
CREATE INDEX idx_pr_risk_verdict_service ON pr_risk_verdict(service_id, pr_number);
```

---

## 6. MongoDB Collections (5)

```javascript
// Collection 1: raw_manifests
db.createCollection("raw_manifests", {
  validator: {
    $jsonSchema: {
      bsonType: "object",
      required: ["service_id", "ecosystem", "raw_content", "uploaded_at"],
      properties: {
        service_id:      { bsonType: "int" },
        ecosystem:       { enum: ["npm","pypi","cargo","go","maven","nuget"] },
        raw_content:     { bsonType: "string" },
        parsed_packages: { bsonType: "array" },  // [{ name, version, is_direct }]
        uploaded_at:     { bsonType: "date" },
        sha256:          { bsonType: "string" }   // dedup: skip if same sha256 exists
      }
    }
  }
});
db.raw_manifests.createIndex({ service_id: 1, uploaded_at: -1 });
db.raw_manifests.createIndex({ sha256: 1 }, { unique: true });

// Collection 2: osv_advisories
db.createCollection("osv_advisories", {
  validator: {
    $jsonSchema: {
      bsonType: "object",
      required: ["osv_id", "raw_osv", "fetched_at"],
      properties: {
        osv_id:     { bsonType: "string" },  // "GHSA-xxxx-xxxx-xxxx"
        raw_osv:    { bsonType: "object" },  // full OSV schema verbatim
        fetched_at: { bsonType: "date" }
      }
    }
  }
});
db.osv_advisories.createIndex({ osv_id: 1 }, { unique: true });
db.osv_advisories.createIndex({ fetched_at: -1 });

// Collection 3: package_embeddings
// Document: { package_id, ecosystem, name, text_used, embedding[768], umap_2d[x,y], cluster_id, computed_at }
db.createCollection("package_embeddings");
db.package_embeddings.createIndex({ package_id: 1 }, { unique: true });
db.package_embeddings.createIndex({ cluster_id: 1 });

// Collection 4: runtime_samples (TTL: 90 days)
// Document: { service_id, scan_id, package_id, version_string, exec_count, observation_window_days, captured_at }
db.createCollection("runtime_samples");
db.runtime_samples.createIndex({ service_id: 1, scan_id: 1 });
db.runtime_samples.createIndex({ captured_at: 1 }, { expireAfterSeconds: 7776000 });

// Collection 5: review_responses (TTL: 30 days)
// Document: { service_id, pr_number, dep_name, model_used, prompt_tokens, verdict, reasoning, responded_at }
db.createCollection("review_responses");
db.review_responses.createIndex({ service_id: 1, pr_number: 1 });
db.review_responses.createIndex({ responded_at: 1 }, { expireAfterSeconds: 2592000 });
```

---

## 7. Project Folder Structure

```
dephgraph/
│
├── .github/
│   └── workflows/
│       ├── ci.yml                        # Lint + test on every PR
│       └── deploy.yml                    # Deploy on main merge
│
├── apps/
│   │
│   ├── web/                              # ── FRONTEND (Next.js 15) ──
│   │   ├── src/
│   │   │   ├── app/
│   │   │   │   ├── (auth)/
│   │   │   │   │   ├── login/page.tsx
│   │   │   │   │   └── register/page.tsx
│   │   │   │   ├── (dashboard)/
│   │   │   │   │   ├── layout.tsx        # Sidebar + topbar
│   │   │   │   │   ├── page.tsx          # / → key metrics overview
│   │   │   │   │   ├── services/
│   │   │   │   │   │   ├── page.tsx      # /services → list
│   │   │   │   │   │   └── [id]/page.tsx # /services/3 → dep graph
│   │   │   │   │   ├── vulnerabilities/page.tsx
│   │   │   │   │   ├── remediation/page.tsx
│   │   │   │   │   ├── clusters/page.tsx
│   │   │   │   │   └── settings/page.tsx
│   │   │   │   ├── api/
│   │   │   │   │   └── webhooks/github/route.ts  # sig validate → forward
│   │   │   │   ├── layout.tsx
│   │   │   │   └── globals.css
│   │   │   │
│   │   │   ├── components/
│   │   │   │   ├── ui/                   # shadcn/ui primitives
│   │   │   │   ├── graph/
│   │   │   │   │   ├── DependencyGraph.tsx   # ReactFlow main graph
│   │   │   │   │   ├── VulnNode.tsx          # Red node for vuln pkgs
│   │   │   │   │   ├── PackageNode.tsx
│   │   │   │   │   └── GraphControls.tsx
│   │   │   │   ├── charts/
│   │   │   │   │   ├── BlastRadiusBar.tsx
│   │   │   │   │   ├── SeverityDonut.tsx
│   │   │   │   │   ├── ClusterScatter.tsx    # UMAP 2D scatter
│   │   │   │   │   └── RemediationProgress.tsx
│   │   │   │   ├── tables/
│   │   │   │   │   ├── VulnTable.tsx
│   │   │   │   │   ├── PrunableTable.tsx
│   │   │   │   │   └── RemediationTable.tsx
│   │   │   │   └── layout/
│   │   │   │       ├── Sidebar.tsx
│   │   │   │       ├── Topbar.tsx
│   │   │   │       └── MetricCard.tsx
│   │   │   │
│   │   │   ├── lib/
│   │   │   │   ├── supabase/
│   │   │   │   │   ├── client.ts         # Browser Supabase client
│   │   │   │   │   └── server.ts         # Server RSC client
│   │   │   │   ├── api.ts                # FastAPI typed fetch wrapper
│   │   │   │   └── types.ts              # Auto-generated from Supabase
│   │   │   │
│   │   │   ├── hooks/
│   │   │   │   ├── useServices.ts
│   │   │   │   ├── useVulnerabilities.ts
│   │   │   │   ├── useBlastRadius.ts
│   │   │   │   ├── useRemediationPlan.ts
│   │   │   │   └── useClusters.ts
│   │   │   │
│   │   │   └── middleware.ts             # Supabase Auth route protection
│   │   │
│   │   ├── public/favicon.ico
│   │   ├── package.json
│   │   ├── tailwind.config.ts
│   │   ├── next.config.ts
│   │   ├── components.json               # shadcn/ui config
│   │   └── tsconfig.json
│   │
│   └── api/                              # ── BACKEND (FastAPI) ──
│       ├── app/
│       │   ├── main.py                   # FastAPI app, CORS, lifespan, routers
│       │   ├── config.py                 # pydantic-settings: reads .env
│       │   ├── database.py               # SQLAlchemy async + Motor + Upstash
│       │   │
│       │   ├── models/
│       │   │   ├── sql.py                # SQLAlchemy 2.0 ORM (all 23 tables)
│       │   │   └── mongo.py              # MongoDB collection helpers
│       │   │
│       │   ├── schemas/                  # Pydantic v2 request/response models
│       │   │   ├── ingest.py
│       │   │   ├── service.py
│       │   │   ├── vulnerability.py
│       │   │   ├── remediation.py
│       │   │   ├── cluster.py
│       │   │   └── pr_gate.py
│       │   │
│       │   ├── routers/
│       │   │   ├── health.py             # GET /health
│       │   │   ├── ingest.py             # POST /ingest/manifest
│       │   │   ├── services.py           # GET /services + dep graph
│       │   │   ├── vulnerabilities.py    # GET /vulnerabilities + blast radius
│       │   │   ├── remediation.py        # GET+POST /remediation/plan
│       │   │   ├── clusters.py           # GET /clusters + recompute
│       │   │   ├── pr_gate.py            # POST /pr-gate/webhook
│       │   │   └── internal.py           # POST /internal/osv-sync (cron)
│       │   │
│       │   └── services/                 # Business logic (no direct DB calls)
│       │       ├── manifest_parser.py    # Parse req.txt/pkg.json/Cargo.toml/go.mod
│       │       ├── osv_fetcher.py        # OSV.dev async batch client
│       │       ├── deps_dev_client.py    # deps.dev metadata client
│       │       ├── blast_radius.py       # Recursive CTE + Redis cache
│       │       ├── set_cover.py          # Greedy NP-hard set cover (Chvátal 1979)
│       │       ├── license_propagator.py # Copyleft contamination query
│       │       ├── cluster_engine.py     # SBERT → UMAP → HDBSCAN → c-TF-IDF
│       │       ├── liveness_agent.py     # sys.settrace-based runtime observer
│       │       └── pr_gate_engine.py     # 3-stage filter orchestrator
│       │
│       ├── migrations/
│       │   ├── 001_initial_schema.sql    # 14 entity + 9 relationship tables
│       │   ├── 002_views.sql             # 5 signature query views
│       │   └── 003_indexes.sql           # FK + query-pattern indexes
│       │
│       ├── tests/
│       │   ├── conftest.py               # Fixtures: test DB, seeded data, mock HTTP
│       │   ├── fixtures/
│       │   │   ├── requirements_flask.txt
│       │   │   ├── package_react.json
│       │   │   └── Cargo_tokio.toml
│       │   ├── test_manifest_parser.py
│       │   ├── test_ingest.py
│       │   ├── test_blast_radius.py
│       │   ├── test_set_cover.py
│       │   ├── test_cluster.py
│       │   └── test_pr_gate.py
│       │
│       ├── requirements.txt
│       ├── Dockerfile
│       └── render.yaml
│
├── scripts/
│   ├── seed_db.py                        # 3 teams, 5 services, 50 pkgs, 15 vulns
│   ├── run_osv_sync.py                   # One-shot full OSV refresh
│   ├── verify_schema.py                  # Assert 23 tables + 5 views + all FKs
│   └── generate_supabase_types.py        # Pull schema → TypeScript types
│
├── .claude/
│   └── INSTRUCTIONS.md                   # Detailed Claude Code rules (see §8)
│
├── CLAUDE.md                             # Root Claude instructions
├── .env.example                          # All required env vars
├── .env.local                            # Your actual values (gitignored)
├── docker-compose.yml                    # Local dev: Postgres + Mongo + Redis
├── turbo.json                            # Turborepo config
├── package.json                          # Root workspace
└── .gitignore
```

---

## 8. Claude Code Instructions

### CLAUDE.md (root — always loaded)

```markdown
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
```

### .claude/INSTRUCTIONS.md (detailed rules)

```markdown
## Code Style

### Python (FastAPI)
- Formatter: Black (line-length=100)
- Imports: isort with profile=black
- Type hints: everywhere including return types
- No bare `except:` — always catch specific exceptions
- Log with structlog, NOT print()

### TypeScript (Next.js)
- Prettier + ESLint next/core-web-vitals
- Prefer const over let; no var
- No `any` — use `unknown` + type narrowing

### SQL
- UPPERCASE keywords
- snake_case for all identifiers
- Always alias tables in multi-table queries

## Database Conventions

### PostgreSQL (Supabase)
- IDs: BIGSERIAL (not UUID — too slow for joins)
- Timestamps: TIMESTAMPTZ always
- Hard delete with ON DELETE CASCADE (no soft deletes)
- RLS: every new table needs ENABLE ROW LEVEL SECURITY + select policy
- New M:N: always a join table, never arrays
- Migrations: numbered, never edit existing

### MongoDB
- ObjectId → str before JSON serialization
- TTL indexes: runtime_samples (90d), review_responses (30d) — already set
- Never store sensitive data here (no RLS)

### Redis
- Key pattern: `dephgraph:{resource}:{id}`
- TTL: blast_radius = 86400s, prompts = 604800s
- Use nx=True for set operations to avoid race conditions

## Testing
- Every router endpoint: happy path + 404 + 422 = minimum 3 tests
- Use pytest-asyncio with @pytest.mark.asyncio
- Mock all external HTTP calls (OSV.dev, deps.dev, Groq) with respx
- Coverage target: ≥70%

## Common Pitfalls
- asyncpg uses $1 $2 placeholders, NOT %s (that's psycopg2)
- Supabase RLS blocks ALL by default — add policies for new tables
- ReactFlow needs <ReactFlowProvider> wrapping — can't use hooks outside it
- Render free tier has NO persistent disk — don't write files in production
- Groq free: 30 req/min — add exponential backoff with jitter
- SBERT model (80MB) downloads on first run — download at startup, not per-request

## Stage 2 PR Gate Decision Logic
- BLOCK if: CVSS ≥ 9.0, OR copyleft license, OR abandonment_score ≥ 80
- ADVISORY if: CVSS 6.0–8.9, OR abandonment 40–79, OR vuln with no fixed_in
- PASS if: no vulns, OR all CVSS < 6.0 with a fix available
```

---

## 9. Phases

### Phase 0 — Setup & Infrastructure (Day 1)

**Tasks**:
1. `npx create-turbo@latest dephgraph` → npm workspaces
2. Create GitHub repo → push initial commit
3. Create accounts: Supabase, MongoDB Atlas, Upstash, Vercel, Render, Groq, cron-job.org
4. Copy `docker-compose.yml` (Postgres + Mongo + Redis)
5. Fill `.env.local` with all connection strings
6. `docker compose up -d` → verify all 3 containers healthy
7. Bootstrap FastAPI: `apps/api/` + `/health` endpoint
8. Bootstrap Next.js: `apps/web/` with Tailwind + shadcn/ui + Supabase client
9. Set up GitHub Actions CI (`ci.yml`)
10. Connect Vercel to repo
11. Connect Render to repo

**E2E Test — Phase 0 Pass Criteria**:
```bash
curl http://localhost:8000/health
# → {"data": {"status": "ok"}, "error": null}

cd apps/web && npm run build
# → Build succeeded

cd apps/api && pytest tests/ -v
# → 0 tests, collection works (no errors)

git push origin main
# → GitHub Actions CI green
```

---

### Phase 1 — Core Schema & Seed Data (Days 2–3)

**Tasks**:
1. Write `migrations/001_initial_schema.sql` (14 entity + 9 relationship tables)
2. Write `migrations/002_views.sql` (5 views)
3. Write `migrations/003_indexes.sql` (FK + query-pattern indexes)
4. Apply to Supabase SQL Editor → Run each migration
5. Enable RLS on all tables + add select policies for authenticated users
6. Write `scripts/verify_schema.py` → asserts 23 tables + 5 views + all FKs
7. Write `scripts/seed_db.py`:
   - 3 teams, 5 services, 50 packages (pypi+npm+cargo), 200 package_versions
   - 3 licenses (MIT, Apache-2.0, GPL-3.0)
   - 15 vulnerabilities (3 CRITICAL, 5 HIGH, 7 MED/LOW)
   - `affects` records linking vulns to versions
   - `depends_on` for a realistic dep graph
   - `resolves_to` pre-computed closure for all 5 services

**E2E Test — Phase 1 Pass Criteria**:
```bash
python scripts/verify_schema.py
# ✅ Tables: 23/23
# ✅ Views: 5/5
# ✅ FK constraints: all valid
# ✅ Indexes: 14/14

python scripts/seed_db.py
# ✅ 3 teams, 5 services, 50 packages, 200 versions, 15 vulns

# Supabase SQL Editor:
SELECT COUNT(*) FROM package_version;   -- 200
SELECT COUNT(*) FROM affects;           -- > 20
SELECT * FROM v_blast_radius LIMIT 5;  -- returns rows
```

---

### Phase 2 — Ingestion Pipeline (Days 4–6)

**Tasks**:
1. `manifest_parser.py` — parsers for `requirements.txt`, `package.json`, `Cargo.toml`, `go.mod`
2. `osv_fetcher.py` — async OSV.dev batch client, `semaphore(10)`, upserts to PostgreSQL + raw JSON to MongoDB
3. `deps_dev_client.py` — async metadata fetch (license, published_at, is_yanked)
4. `routers/ingest.py`:
   - `POST /ingest/manifest` body: `{service_id, ecosystem, content, filename}`
   - Returns: `{packages_parsed, packages_inserted, vulns_fetched, duration_ms}`
   - `POST /ingest/scan/{service_id}` → re-run OSV sync for service
5. `models/sql.py` — SQLAlchemy ORM for all 23 tables
6. Test fixtures: `requirements_flask.txt`, `package_react.json`, `Cargo_tokio.toml`

**E2E Test — Phase 2 Pass Criteria**:
```python
import httpx
content = open("tests/fixtures/requirements_flask.txt").read()
r = httpx.post("http://localhost:8000/ingest/manifest", json={
    "service_id": 1, "ecosystem": "pypi",
    "content": content, "filename": "requirements.txt"
})
assert r.status_code == 200
d = r.json()["data"]
assert d["packages_parsed"] > 5
assert d["duration_ms"] < 60_000  # under 60s
```

---

### Phase 3 — Analysis Engine (Days 7–9)

**Tasks**:
1. `blast_radius.py` — run `v_blast_radius` filtered by service, cache in Redis (24h TTL)
2. `set_cover.py` — greedy loop (NP-hard but H(n)≈ln(n)+1 guarantee, Chvátal 1979)
3. `routers/vulnerabilities.py` — blast radius + paginated vuln list
4. `routers/remediation.py` — generate + persist set-cover plan

**E2E Test — Phase 3 Pass Criteria**:
```python
# Known optimal: pkg_A covers {v1,v2}, pkg_B covers {v3}
result = greedy_set_cover(
    open_vulns={1, 2, 3},
    pkg_coverage={"pkg_A": {1,2}, "pkg_B": {3}, "pkg_C": {1}}
)
assert result[0]["package"] == "pkg_A"
assert result[-1]["cumulative_coverage_pct"] == 100.0
assert len(result) == 2  # optimal

# Overall test suite:
pytest apps/api/tests/ -v --cov=app
# ✅ ≥70% coverage
```

---

### Phase 4 — Dashboard Frontend (Days 10–13)

**Tasks**:
1. Install + configure shadcn/ui, ReactFlow
2. Supabase Auth middleware (protect all dashboard routes)
3. Build 5 pages:
   - **`/`**: 4 MetricCards + SeverityDonut chart
   - **`/services/[id]`**: ReactFlow dep graph (red = vuln) + PrunableTable
   - **`/vulnerabilities`**: VulnTable, filters, CVSS badge, sortable
   - **`/remediation`**: Greedy plan steps + BlastRadiusBar coverage %
   - **`/clusters`**: UMAP ScatterChart + cluster legend with keywords
4. Generate TypeScript types: `supabase gen types typescript --project-id [ref] > src/lib/types.ts`

**E2E Test — Phase 4 Pass Criteria**:
```typescript
test("home loads under 3s", async ({ page }) => {
  const start = Date.now();
  await page.goto("/");
  await expect(page.getByText("Services")).toBeVisible();
  expect(Date.now() - start).toBeLessThan(3000);
});

test("dep graph renders nodes", async ({ page }) => {
  await page.goto("/services/1");
  await expect(page.locator(".react-flow__node").first()).toBeVisible();
});

test("vuln table shows critical badge", async ({ page }) => {
  await page.goto("/vulnerabilities");
  await expect(page.getByText("CRITICAL")).toBeVisible();
});
```

---

### Phase 5 — Semantic Clustering (Days 14–16)

**Tasks**:
1. `cluster_engine.py`:
   ```python
   # Pipeline:
   # 1. Pull all packages from PostgreSQL
   # 2. SBERT encode: SentenceTransformer("all-MiniLM-L6-v2"), batch_size=64
   # 3. UMAP 2D: n_components=2, random_state=42
   # 4. HDBSCAN: min_cluster_size=5, noise-tolerant (GROUPED_INTO partial participation)
   # 5. c-TF-IDF per cluster → human-readable label (top 3 keywords)
   # 6. Upsert clusters to PostgreSQL semantic_cluster table
   # 7. Store embeddings + umap_2d in MongoDB package_embeddings
   ```
2. `routers/clusters.py`: list clusters + members with umap_2d coords, trigger recompute
3. Frontend `ClusterScatter.tsx`: Recharts ScatterChart, color=cluster_id, hover=package name
4. Wire `v_cluster_redundancy` to dashboard: "3 HTTP clients in auth-service"

**E2E Test — Phase 5 Pass Criteria**:
```python
r = httpx.post("http://localhost:8000/clusters/recompute", timeout=5)
assert r.status_code == 202  # Accepted, background job

time.sleep(45)  # Wait for pipeline

r = httpx.get("http://localhost:8000/clusters")
clusters = r.json()["data"]
assert len(clusters) >= 3
assert all(c["label"] != "" for c in clusters)
assert all(c["member_count"] > 0 for c in clusters)
```

---

### Phase 6 — PR Gate & GitHub Integration (Days 17–19)

**Tasks**:
1. Create GitHub OAuth App + GitHub App (needs `checks:write`)
2. `apps/web/.../api/webhooks/github/route.ts` — validate HMAC-SHA256, forward to FastAPI
3. `pr_gate_engine.py` — 3-stage orchestrator:
   - Stage 1: `SELECT 1 FROM affects WHERE package_id=$id AND version_string=$ver LIMIT 1`
   - Stage 2: Full SQL scoring query → BLOCK/ADVISORY/PASS decision
   - Stage 3: Build compressed prompt (≤300 tokens), Groq call, parse verdict, store in MongoDB
4. `routers/pr_gate.py` → run pipeline → write `pr_review`+`pr_risk_verdict` → GitHub Checks API

**E2E Test — Phase 6 Pass Criteria**:
```python
r = httpx.post("http://localhost:8000/pr-gate/webhook",
    json=mock_pr_payload,
    headers={"X-GitHub-Event": "pull_request",
             "X-Hub-Signature-256": "sha256=..."})
assert r.status_code == 200
data = r.json()["data"]
assert data["verdict"] in ["PASS", "ADVISORY", "BLOCK"]
assert data["stage_reached"] in [1, 2, 3]
assert data["processing_ms"] < 30_000
```

---

### Phase 7 — Polish & Production Deploy (Days 20–21)

**Tasks**:
1. Rate limiting: `slowapi` — 100 req/min per IP
2. Global error handler + structured JSON logging with `structlog`
3. `EXPLAIN ANALYZE` all views → add missing indexes
4. RLS audit: verify all 23 tables have policies
5. Loading states + error boundaries on all frontend pages
6. Vercel deploy: `vercel --prod`, set env vars
7. Render deploy: push `render.yaml`, set env vars, trigger deploy
8. cron-job.org: 3 jobs (OSV sync, cluster recompute, health ping)

**E2E Test — Phase 7 Pass Criteria (Full Pipeline on Production)**:
```bash
BASE="https://dephgraph-api.onrender.com"

# Health
curl $BASE/health | python3 -c "import sys,json; assert json.load(sys.stdin)['data']['status']=='ok'"
echo "✅ API healthy"

# Ingest
curl -X POST $BASE/ingest/manifest \
  -H "Authorization: Bearer $TEST_TOKEN" \
  -d '{"service_id":1,"ecosystem":"pypi","content":"flask==2.0.1\n","filename":"requirements.txt"}'
echo "✅ Ingestion working"

# Blast radius
curl "$BASE/blast-radius/1" -H "Authorization: Bearer $TEST_TOKEN" | \
  python3 -c "import sys,json; assert json.load(sys.stdin)['data'] is not None"
echo "✅ Blast radius working"

# Remediation
curl "$BASE/remediation/plan/1" -H "Authorization: Bearer $TEST_TOKEN" | \
  python3 -c "import sys,json; assert isinstance(json.load(sys.stdin)['data'], list)"
echo "✅ Remediation plan working"

# Frontend
curl -s -o /dev/null -w "%{http_code}" https://dephgraph.vercel.app | grep -q "200"
echo "✅ Frontend live"

echo "🎉 DephGraph is live."
```

---

## 10. All Dependencies

### Backend `requirements.txt`
```
# Web framework
fastapi==0.115.0
uvicorn[standard]==0.32.0
python-multipart==0.0.12

# PostgreSQL
asyncpg==0.29.0
sqlalchemy[asyncio]==2.0.36

# MongoDB
motor==3.6.0
pymongo[srv]==4.10.1

# Cache
upstash-redis==1.3.0

# Validation & config
pydantic==2.9.2
pydantic-settings==2.6.1

# HTTP client
httpx==0.27.2

# Clustering pipeline
sentence-transformers==3.3.1
umap-learn==0.5.7
hdbscan==0.8.39
numpy==2.1.3
scikit-learn==1.5.2
torch==2.5.1

# LLM (Stage 3)
groq==0.12.0

# Version parsing
packaging==24.2

# Auth
supabase==2.10.0
python-jose[cryptography]==3.3.0

# Logging & rate limiting
structlog==24.4.0
slowapi==0.1.9

# Testing
pytest==8.3.3
pytest-asyncio==0.24.0
pytest-cov==6.0.0
respx==0.21.1
```

### Frontend `package.json`
```json
{
  "dependencies": {
    "next": "15.0.3",
    "react": "19.0.0",
    "react-dom": "19.0.0",
    "typescript": "5.6.3",
    "@supabase/supabase-js": "2.46.1",
    "@supabase/ssr": "0.5.1",
    "reactflow": "11.11.4",
    "recharts": "2.13.3",
    "tailwindcss": "3.4.14",
    "class-variance-authority": "0.7.0",
    "clsx": "2.1.1",
    "tailwind-merge": "2.5.4",
    "@radix-ui/react-dialog": "1.1.2",
    "@radix-ui/react-dropdown-menu": "2.1.2",
    "@radix-ui/react-select": "2.1.2",
    "@radix-ui/react-tabs": "1.1.1",
    "@radix-ui/react-toast": "1.2.2",
    "@radix-ui/react-tooltip": "1.1.3",
    "lucide-react": "0.460.0",
    "next-themes": "0.4.3",
    "date-fns": "4.1.0",
    "sonner": "1.7.0"
  },
  "devDependencies": {
    "eslint": "9.14.0",
    "eslint-config-next": "15.0.3",
    "@playwright/test": "1.48.2",
    "@types/node": "22.9.0",
    "@types/react": "19.0.0",
    "@types/react-dom": "19.0.0",
    "autoprefixer": "10.4.20",
    "postcss": "8.4.49"
  }
}
```

---

## 11. Environment Variables

```bash
# .env.example — copy to .env.local and fill all values

# Supabase
SUPABASE_URL=https://xxxxxxxxxxxx.supabase.co
SUPABASE_ANON_KEY=eyJ...
SUPABASE_SERVICE_ROLE_KEY=eyJ...   # NEVER expose to browser
DATABASE_URL=postgresql+asyncpg://postgres:[password]@db.xxxxxxxxxxxx.supabase.co:5432/postgres

# MongoDB Atlas
MONGO_URI=mongodb+srv://dephgraph:[password]@cluster0.xxxxx.mongodb.net/dephgraph?retryWrites=true&w=majority
MONGO_DB_NAME=dephgraph

# Upstash Redis
REDIS_URL=rediss://default:[token]@xxxx.upstash.io:6380

# GitHub
GITHUB_APP_ID=123456
GITHUB_PRIVATE_KEY_BASE64=LS0t...   # base64-encoded PEM private key
GITHUB_WEBHOOK_SECRET=your-random-webhook-secret

# Groq (free tier, Stage 3)
GROQ_API_KEY=gsk_xxx...

# App
ENVIRONMENT=development
SECRET_KEY=your-32-char-random-key
INTERNAL_CRON_SECRET=another-random-secret   # guards /internal/* endpoints

# Frontend (safe to expose)
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_SUPABASE_URL=https://xxxxxxxxxxxx.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=eyJ...
```

---

## 12. GitHub Actions CI

```yaml
# .github/workflows/ci.yml
name: CI
on:
  pull_request:
    branches: [main]
  push:
    branches: [main]

jobs:
  backend-test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:15
        env:
          POSTGRES_PASSWORD: test
          POSTGRES_DB: dephgraph_test
        ports: ["5432:5432"]
        options: --health-cmd pg_isready --health-interval 10s --health-retries 5
      mongo:
        image: mongo:7
        ports: ["27017:27017"]

    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12", cache: "pip" }
      - run: pip install -r apps/api/requirements.txt
      - name: Apply schema
        run: |
          PGPASSWORD=test psql -h localhost -U postgres -d dephgraph_test -f apps/api/migrations/001_initial_schema.sql
          PGPASSWORD=test psql -h localhost -U postgres -d dephgraph_test -f apps/api/migrations/002_views.sql
          PGPASSWORD=test psql -h localhost -U postgres -d dephgraph_test -f apps/api/migrations/003_indexes.sql
      - name: Run tests
        run: pytest apps/api/tests/ -v --cov=app --cov-fail-under=70
        env:
          DATABASE_URL: postgresql+asyncpg://postgres:test@localhost:5432/dephgraph_test
          MONGO_URI: mongodb://localhost:27017
          MONGO_DB_NAME: dephgraph_test
          ENVIRONMENT: test
          GROQ_API_KEY: test_key

  frontend-build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: "20", cache: "npm" }
      - run: npm ci --workspace=apps/web
      - run: npm run typecheck --workspace=apps/web
        env:
          NEXT_PUBLIC_API_URL: http://localhost:8000
          NEXT_PUBLIC_SUPABASE_URL: https://placeholder.supabase.co
          NEXT_PUBLIC_SUPABASE_ANON_KEY: placeholder
      - run: npm run build --workspace=apps/web
        env:
          NEXT_PUBLIC_API_URL: http://localhost:8000
          NEXT_PUBLIC_SUPABASE_URL: https://placeholder.supabase.co
          NEXT_PUBLIC_SUPABASE_ANON_KEY: placeholder
```

---

## 13. docker-compose.yml (Local Dev)

```yaml
version: "3.9"
services:
  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_USER: dephgraph
      POSTGRES_PASSWORD: dephgraph
      POSTGRES_DB: dephgraph
    ports: ["5432:5432"]
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./apps/api/migrations:/docker-entrypoint-initdb.d

  mongo:
    image: mongo:7
    ports: ["27017:27017"]
    volumes:
      - mongo_data:/data/db

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
    command: redis-server --appendonly yes
    volumes:
      - redis_data:/data

volumes:
  postgres_data:
  mongo_data:
  redis_data:
```

---

## 14. render.yaml (Render Deploy Config)

```yaml
services:
  - type: web
    name: dephgraph-api
    env: python
    region: oregon
    rootDir: apps/api
    buildCommand: pip install -r requirements.txt
    startCommand: uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1
    healthCheckPath: /health
    autoDeploy: true
    envVars:
      - key: DATABASE_URL
        sync: false
      - key: MONGO_URI
        sync: false
      - key: REDIS_URL
        sync: false
      - key: GROQ_API_KEY
        sync: false
      - key: GITHUB_APP_ID
        sync: false
      - key: GITHUB_PRIVATE_KEY_BASE64
        sync: false
      - key: GITHUB_WEBHOOK_SECRET
        sync: false
      - key: INTERNAL_CRON_SECRET
        sync: false
      - key: ENVIRONMENT
        value: production
```

---

## 15. Complete API Reference

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/health` | None | Health check (Render warm-up) |
| POST | `/ingest/manifest` | JWT | Upload + parse lockfile for service |
| POST | `/ingest/scan/{service_id}` | JWT | Re-run OSV sync for service |
| GET | `/services` | JWT | List all services for team |
| GET | `/services/{id}/graph` | JWT | Full dep graph (nodes + edges for ReactFlow) |
| GET | `/services/{id}/prunable` | JWT | Zero exec_count packages |
| GET | `/blast-radius/{service_id}` | JWT | Transitive vuln exposure, Redis-cached |
| GET | `/vulnerabilities` | JWT | Filtered, paginated vuln list |
| GET | `/vulnerabilities/{id}` | JWT | Single vuln with full OSV details |
| GET | `/remediation/plan/{team_id}` | JWT | Greedy set-cover plan |
| POST | `/remediation/plan` | JWT | Persist plan to DB |
| PATCH | `/remediation/plan/{id}/actions/{seq}` | JWT | Mark action DONE/SKIPPED |
| GET | `/clusters` | JWT | List clusters with count + labels |
| GET | `/clusters/{id}/members` | JWT | Packages with umap_2d coords |
| POST | `/clusters/recompute` | JWT | Trigger full clustering pipeline |
| POST | `/pr-gate/webhook` | HMAC | GitHub webhook receiver |
| GET | `/pr-gate/verdicts/{service_id}` | JWT | Recent PR verdicts |
| POST | `/internal/osv-sync` | Cron secret | Cron: refresh all OSV data |

---

## 16. Timeline Summary

| Phase | Focus | Duration | Cumulative |
|-------|-------|----------|------------|
| 0 — Setup | Repo, accounts, local dev, CI | Day 1 | Day 1 |
| 1 — Schema | 23 tables, views, seed data | Days 2–3 | Day 3 |
| 2 — Ingestion | Parser + OSV API + FastAPI | Days 4–6 | Day 6 |
| 3 — Analysis | Blast radius, set cover, prune | Days 7–9 | Day 9 |
| 4 — Dashboard | Next.js 5 pages + ReactFlow | Days 10–13 | Day 13 |
| 5 — Clustering | SBERT + UMAP + HDBSCAN | Days 14–16 | Day 16 |
| 6 — PR Gate | 3-stage filter + GitHub Checks | Days 17–19 | Day 19 |
| 7 — Polish | Rate limit, error handling, deploy | Days 20–21 | Day 21 |

**Total: ~3 weeks** at 2–4 hours/day.

---

## 17. What This Achieves

By Phase 7, DephGraph answers questions no existing free tool answers:

> **"Which single upgrade fixes the most vulnerabilities across all my services?"**
> → Greedy set-cover plan with coverage % per step.

> **"Which packages are installed but never actually called at runtime?"**
> → `v_prunable`: in `resolves_to` with no `observes` record where `is_live = TRUE`.

> **"Do I have 3 HTTP client libraries that all do the same thing?"**
> → `v_cluster_redundancy`: packages in the same HDBSCAN cluster within the same service.

> **"This PR adds a new dependency — should I block it?"**
> → 3-stage gate: manifest check → SQL verdict → LLM advisory → GitHub Check status.

---

*Built on 14 entities · 16 relationships · 66 attributes · 23 tables · 5 MongoDB collections.*

*Glorious purpose.*
