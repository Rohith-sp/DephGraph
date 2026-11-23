# DephGraph — Data Flow Diagrams (DFDs)

Three balanced levels, generated with the Eraser diagram-as-code renderer. The `.eraser`
files are the source of truth; `render.py` regenerates the PNGs and refreshes the Eraser
editor links in `rendered.json`.

| Level | Diagram | PNG | Source |
|---|---|---|---|
| 0 — Context | The whole product as one process, with its external data exchanges. No internals, no stores. | [`images/dfd-level-0-context.png`](images/dfd-level-0-context.png) | [`level-0-context.eraser`](level-0-context.eraser) |
| 1 — System | The Level 0 process exploded into seven sub-processes; the three data stores appear. | [`images/dfd-level-1-system.png`](images/dfd-level-1-system.png) | [`level-1-system.eraser`](level-1-system.eraser) |
| 2 — Process 2.0 | Manifest and lockfile ingestion zoomed to 14 numbered sub-processes with local stores and failure paths. | [`images/dfd-level-2-ingestion.png`](images/dfd-level-2-ingestion.png) | [`level-2-ingestion.eraser`](level-2-ingestion.eraser) |

Level 3+ is not drawn. The remaining processes (`3.0` enrichment, `4.0` analysis, `6.0`
PR gate) are single-decision routines in the code; their logic is documented in
[`DephGraph_BUILD_PRD.md`](../DephGraph_BUILD_PRD.md) §3.2–3.3 and in the service modules
under `apps/api/app/services/`, which is the right stopping point for a DFD.

## Notation

| Shape / colour | DFD meaning |
|---|---|
| Blue rectangle | External entity (source or sink outside the system boundary) |
| Orange circle | Process — numbered, with a verb phrase |
| Green cylinder | Data store |
| Arrow | Data flow, labelled with the payload |
| Red arrow | Alternative / failure path (Level 2 only) |
| Grey box | Phase / lane grouping (not a process) |

## Level 0 — context

Single process `0 DephGraph`. External entities and their exchanges:

- **E1 Security Engineer / Developer** — sends manifest uploads, scan and analysis
  requests, team and alert settings; receives dashboard pages, the dependency graph,
  vulnerability views and remediation plans.
- **E2 GitHub App** — sends HMAC-signed push / `pull_request` webhooks; receives PR diff
  requests and check-run conclusions (pass / advisory / block).
- **E3 OSV.dev**, **E4 deps.dev** — receive package/version lookups; return advisories
  (CVE/GHSA, CVSS, fixed-in) and licence, release and maintainer metadata.
- **E5 Groq LLM** — receives the Stage 3 risk prompt; returns an advisory verdict.
- **E6 GitHub Actions** — scheduled OSV sync, maintainer sync, clustering, PR-gate retry,
  keepalive and backups.
- **E7 Supabase Auth** — JWKS public keys used to verify bearer tokens.
- **E8 Runtime collectors (Python, Node)** — execution-count samples authenticated with a
  service token.

## Level 1 — sub-processes and stores

| ID | Sub-process | Code anchor |
|---|---|---|
| 1.0 | Serve Dashboard & API (JWT, teams, alerts, rate limit) | `apps/web/src/app`, `apps/api/app/main.py`, `auth.py`, `limiter.py`, `routers/teams.py`, `routers/me.py`, `routers/alerts.py` |
| 2.0 | Ingest Manifests & Lockfiles | `apps/api/app/routers/ingest.py`, `services/manifest_parser.py` |
| 3.0 | Enrich Registry & Vulnerability Data | `services/osv_fetcher.py`, `deps_dev_client.py`, `maintainers.py` |
| 4.0 | Analyse Dependency Graph | `services/blast_radius.py`, `set_cover.py`, `packages.py`, `queries.py` |
| 5.0 | Cluster Equivalent Packages | `services/cluster_engine.py`, `cluster_trigger.py` |
| 6.0 | Gate Pull Requests (queued) | `routers/pr_gate.py`, `services/pr_gate_queue.py`, `pr_gate_engine.py`, `github.py` |
| 7.0 | Record Runtime Observations | `routers/runtime.py`, `services/runtime.py`, `collectors/` |

| ID | Data store | Contents |
|---|---|---|
| D1 | PostgreSQL (Supabase) | 31 tables (23 core + 8 operational), views, row-level security by team |
| D2 | MongoDB Atlas | `raw_manifests`, `osv_advisories`, `package_embeddings`, `runtime_samples`, `review_responses` |
| D3 | Redis (Upstash) | data generations for cache invalidation, rate-limit counters, job locks, PR-gate queue, LLM replies |

Flows leaving the Level 0 process are preserved here: E1 talks to 1.0, E2 to 6.0,
E3/E4 to 3.0, E5 to 6.0, E6 to 3.0, 5.0 and 6.0, E7 to 1.0, E8 to 7.0. Inputs and
outputs balance; the stores are new at this level, which is what distinguishes Level 1
from Level 0.

## Level 2 — process 2.0, manifest and lockfile ingestion

Balanced against the Level 1 box: inputs are E1 (upload/replay), E3 (advisories),
E4 (metadata), D1/D2 (reads); outputs are the E1 `IngestResult`, D1/D2 rows and the D3
generation bumps.

1. **2.1** Receive a batch of manifest files (or a replay id) plus the target environment.
2. **2.2** Verify the service and the caller's team membership (404), then compute SHA-256.
3. **2.3** Upsert one `raw_manifests` document per file per `(service_id, sha256)` with
   status `received`. A Mongo outage here is a 503, so Postgres is never touched without a
   raw copy.
4. **2.4** Parse every document — `requirements.txt`, `package.json`, `Cargo.toml`,
   `go.mod` and lockfiles — into ecosystem groups with their locked transitive edges.
   Malformed or unversioned input produces 422.
5. **2.5–2.7** deps.dev metadata and OSV advisories are fetched concurrently, then raw OSV
   JSON is written to `osv_advisories` before any relational write.
6. **2.8–2.11** One Postgres transaction: advisory lock, upsert packages and versions for
   each ecosystem group, apply licences and metadata, upsert vulnerabilities and `affects`,
   insert the deployment (with its environment), `resolves_to` and per-deployment
   `deployment_edge` rows, then record `scan_run`.
7. **2.12–2.14** Mark each raw document `projected` (or `failed`), bump the Redis team and
   catalogue data generations after the commit, and return `manifest_id`, `deploy_seq`,
   counts and duration.

Failure paths: 404/503 at 2.2, 422 at 2.4, and transaction rollback to `failed` at 2.12.

## Regenerate / edit

```bash
python3 dfd/render.py          # re-renders all three PNGs and rendered.json
```

The renderer posts the Eraser DSL to `https://app.eraser.io/api/render/elements`. No
key is needed for basic rendering (small watermark); set `ERASER_API_TOKEN` for
watermark-free, higher-resolution output. `rendered.json` holds the image URLs and a
`createEraserFileUrl` for each diagram — open that link to load the diagram into the
Eraser editor and rearrange nodes by hand if you want a different page layout. Eraser's
auto-layout does the initial placement; the labels and flows are the authored part.

## Eraser MCP

The Eraser MCP server (`https://app.eraser.io/api/mcp`) is not needed to regenerate
these files, but it can create and edit them directly in an Eraser workspace. It has
been added to `~/.config/opencode/opencode.jsonc`; restart OpenCode and complete the
OAuth sign-in when prompted, then ask the agent to create/update the diagrams in Eraser.
Remove the `mcp.eraser` block from that file to disable it.
