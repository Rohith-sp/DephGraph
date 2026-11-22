# DephGraph — coursework phases 4–6

This note supplements the ER, relational, normalization, and relational-algebra work. The
coursework database examples use MySQL 8.4 and `er-diagram/schema.sql`; the running application uses
the separate PostgreSQL schema described in [the schema mapping](../er-diagram/README.md#course-schema-and-product-schema).
PostgreSQL is the system of record (team decision, 22 November 2026); the MySQL schema is the
coursework demonstration of the normalised design.

## Phase 4 — SQL implementation

For a **fresh, disposable course database**, initialize the schema and sample data, then run the
read-only queries:

```bash
scripts/mysql.sh run er-diagram/schema.sql
scripts/mysql.sh run sql/seed.sql
scripts/mysql.sh run sql/queries.sql
```

`er-diagram/schema.sql` starts with `DROP DATABASE`; `sql/seed.sql` truncates its sample tables.
Do not run either against a database containing data you need to keep. If the course schema and
seed are already loaded, run only `scripts/mysql.sh run sql/queries.sql`.

[`sql/queries.sql`](../sql/queries.sql) holds the SQL translations of Q1–Q7; each prints the result
expected from [`relational-algebra/README.md`](../relational-algebra/README.md) above its own output.
[`sql/complex_queries.sql`](../sql/complex_queries.sql) (after [`sql/seed_extra.sql`](../sql/seed_extra.sql))
covers nested and correlated subqueries, `EXISTS`, `NOT EXISTS`, `IN`, `ALL`, `ANY`, outer joins,
`GROUP BY`, `HAVING`, views and a recursive CTE; [`sql/README.md`](../sql/README.md) has the outputs.
[`sql/queries_alt.sql`](../sql/queries_alt.sql) is an alternative formulation of Q1–Q7 plus extra
examples; it does not alter sample data, and its Q7 uses the fixed reference date 2026-11-02 so its
expected waived/open rows remain reproducible after the sample waiver expires.

Keep the generated output or screenshots outside source control if desired; the MySQL helper writes
logs under the gitignored `logs/` directory. Include the command, MySQL version, and any unexpected
error in the report.

## Phase 5 — transactions, ACID, and concurrency

### Transaction boundary in the product

The manifest endpoint in `apps/api/app/routers/ingest.py` fetches OSV and deps.dev data before
opening the write transaction. It then groups the PostgreSQL package, version, metadata, advisory,
deployment, resolution, and scan writes inside `AsyncSession.begin()`. A database error in that
block rolls those PostgreSQL changes back together.

This is **not a distributed transaction** across PostgreSQL, upstream APIs, and MongoDB:

- upstream requests happen before the PostgreSQL write transaction;
- MongoDB copies of raw advisories and manifests are attempted after the PostgreSQL commit and are
  best-effort;
- therefore a MongoDB failure does not roll back PostgreSQL. Re-running the operation/recompute is
  the recovery path for those copies.

[`sql/transaction_demo.sql`](../sql/transaction_demo.sql) demonstrates the narrower database
atomicity guarantee with two successful inserts followed by a deliberate foreign-key error and a
rollback. Run it with `scripts/mysql.sh run --force sql/transaction_demo.sql`; the error is expected,
and the final counts should match the initial counts with zero demo rows remaining.

### ACID examples

| Property | DephGraph example |
|---|---|
| Atomicity | The PostgreSQL writes for one manifest are committed together or rolled back together. The MongoDB copy is outside that boundary. |
| Consistency | Foreign keys, unique keys, checks, and transaction logic preserve valid package/version/deployment relationships. |
| Isolation | Concurrent ingestion and remediation reads are governed by PostgreSQL's configured isolation; the MySQL coursework demos below use InnoDB isolation explicitly. |
| Durability | Once the database reports commit success, PostgreSQL's durability configuration protects the committed transaction across process restart; backups are a separate operational concern. |

### Schedules and conflict serializability

Let `X` and `Y` be two rows. `R_i(X)` means transaction `T_i` reads X and `W_i(X)` means it writes
X. Add an edge `T_i → T_j` when a conflicting operation by `T_i` occurs before one by `T_j` on the
same item and at least one operation is a write.

**Serial schedule:** `R1(X), W1(X), R2(X), W2(X)`. The graph has only `T1 → T2`, so it is
conflict-serializable in the order T1, T2.

**Non-serializable interleaving:** `R1(X), R2(Y), W1(Y), W2(X)`.

- `R2(Y)` before `W1(Y)` gives `T2 → T1`.
- `R1(X)` before `W2(X)` gives `T1 → T2`.
- The graph has a cycle, so this schedule is not conflict-serializable.

**Serializable interleaving:** T1 performs `W(X), W(Y)` and T2 performs `R(X), W(Z)`. The schedule
`W1(X), R2(X), W1(Y), W2(Z)` interleaves the transactions. The only conflict is `W1(X)` before
`R2(X)`, giving the acyclic graph `T1 → T2`; it is conflict-serializable in that order.

For an application example, let T1 be an OSV sync that inserts/updates an `affects` row, and T2 a
remediation calculation that reads that row. If T1 commits before T2 reads it, the ordering is
T1 → T2. When describing a real schedule, list the actual reads and writes; do not infer
serializability from the feature names alone.

### InnoDB locks, `SELECT ... FOR UPDATE`, and deadlock

Use the sample database and two terminals, each running `scripts/mysql.sh shell`. These statements
make no lasting value changes; roll back both sessions.

Two-phase locking (2PL) separates a transaction into a **growing phase**, where it acquires locks
but does not release them, and a **shrinking phase**, where it releases locks and acquires no new
ones. InnoDB's row locks and `SELECT ... FOR UPDATE` participate in its locking protocol; the
lock-wait and deadlock examples below demonstrate why transactions should acquire resources in a
consistent order and keep lock-holding transactions short.

**Lock wait demo**

Session A:

```sql
USE dephgraph;
START TRANSACTION;
UPDATE package SET package_name = package_name WHERE package_id = 100;
-- Keep the transaction open.
```

Session B:

```sql
USE dephgraph;
START TRANSACTION;
UPDATE package SET source_repo_url = source_repo_url WHERE package_id = 100;
-- This waits for A's row lock.
```

Commit or roll back A; B's update then completes. Roll back B.

**Explicit row lock and isolation**

```sql
SET TRANSACTION ISOLATION LEVEL REPEATABLE READ;
START TRANSACTION;
SELECT package_id, package_name
FROM package
WHERE package_id = 100
FOR UPDATE;
ROLLBACK;
```

InnoDB's default isolation level is commonly `REPEATABLE READ`; check the server rather than
assuming:

```sql
SELECT @@transaction_isolation;
```

**Deadlock demo**

Session A:

```sql
START TRANSACTION;
UPDATE team SET team_name = team_name WHERE team_id = 1;
-- After Session B locks team 2:
UPDATE team SET team_name = team_name WHERE team_id = 2;
-- After the deadlock victim is reported, roll back this session.
ROLLBACK;
```

Session B:

```sql
START TRANSACTION;
UPDATE team SET team_name = team_name WHERE team_id = 2;
-- After Session A requests team 2:
UPDATE team SET team_name = team_name WHERE team_id = 1;
-- InnoDB should report error 1213 for one victim; then:
ROLLBACK;
```

The second update in each session creates opposite lock ordering. InnoDB detects the cycle and
rolls back one transaction; the surviving transaction can proceed. Exact victim selection is not
guaranteed.

Inspect current locks while the transactions are waiting:

```sql
SELECT ENGINE_TRANSACTION_ID, OBJECT_SCHEMA, OBJECT_NAME, INDEX_NAME,
       LOCK_TYPE, LOCK_MODE, LOCK_STATUS, LOCK_DATA
FROM performance_schema.data_locks
WHERE OBJECT_SCHEMA = DATABASE();

SELECT r.ENGINE_TRANSACTION_ID AS waiting_trx,
       r.OBJECT_NAME, r.INDEX_NAME, r.LOCK_DATA,
       b.ENGINE_TRANSACTION_ID AS blocking_trx
FROM performance_schema.data_lock_waits w
JOIN performance_schema.data_locks r
  ON r.ENGINE = w.ENGINE
 AND r.ENGINE_LOCK_ID = w.REQUESTING_ENGINE_LOCK_ID
JOIN performance_schema.data_locks b
  ON b.ENGINE = w.ENGINE
 AND b.ENGINE_LOCK_ID = w.BLOCKING_ENGINE_LOCK_ID
WHERE r.OBJECT_SCHEMA = DATABASE();
```

Run the observation queries in a third connection while the wait is active. The rows disappear
when the transactions finish.

## Phase 6 — NoSQL

Legend: **[implemented]** = in the code and checked on the running stack on 22 Nov 2026.
**[design]** = a Unit V concept explained for DephGraph; not built or deployed here.

DephGraph is polyglot. PostgreSQL is the system of record (23 tables, constraints, joins). MongoDB
is the raw layer (documents as received, plus derived vectors). Redis is cache and lock. The
database name is `dephgraph` (`mongo_db_name` in `apps/api/app/config.py`).

### 1. NoSQL motivation (why not only Postgres)

- Upstream payloads (OSV advisories, uploaded manifests) have a shape that changes without notice.
  Storing them verbatim in a document avoids a schema migration every time OSV adds a field.
- Some data is one blob read and written whole: a 384-number embedding, a model prompt/response.
  Rows and joins add nothing for it.
- Hot, disposable results (blast radius per service) need millisecond reads and expiry, which a
  key-value store gives directly.
- Cost: no foreign keys, no cross-document transactions in our usage, no joins. So nothing in
  Mongo or Redis is authoritative.

### 2. Aggregate data models [implemented]

An aggregate is a unit that is read and written as a whole. Each Mongo document is one:

- `raw_manifests`: one uploaded file plus its processing state.
- `osv_advisories`: one advisory with its full upstream JSON.
- `package_embeddings`: one package's vector and 2-D coordinates.
- `runtime_samples`: one agent batch (many samples inside one document).
- `review_responses`: one model prompt and its response.

Embedding arrays inside the document (`samples`, `parsed_packages`) are the aggregate idea: the
child items never need to be queried alone, so they live inside the parent. The dependency graph is
the opposite case (many-to-many, queried by traversal), so it stays relational in `depends_on`.

### 3. The five MongoDB collections [implemented]

Indexes are created at startup by `ensure_indexes()` in `apps/api/app/models/mongo.py`.

| Collection | Purpose | Writer | Key fields | Indexes |
|---|---|---|---|---|
| `raw_manifests` | Raw uploaded manifest, kept before anything reaches Postgres | `routers/ingest.py`: `_store_manifest` (upsert, status `received`), `_mark` (`projected` / `failed`) | `service_id`, `sha256`, `ecosystem`, `filename`, `raw_content`, `uploaded_at`, `status`, `deploy_seqs`, `parsed_packages`, `error` | `(service_id, uploaded_at desc)`; **unique** `(service_id, sha256)` |
| `osv_advisories` | Verbatim OSV advisory JSON | `services/osv_fetcher.py`: `store_raw` (`replace_one`, upsert) | `osv_id`, `raw_osv`, `fetched_at` | **unique** `osv_id`; `fetched_at desc` |
| `package_embeddings` | Derived vector per package, for clustering and the 2-D map | `services/cluster_engine.py`: recompute `bulk_write` of `UpdateOne` upserts | `package_id`, `embedding` (384 floats), `umap_2d`, `cluster_id` | **unique** `package_id`; `cluster_id` |
| `runtime_samples` | Raw runtime-agent batch (which packages executed) | `services/runtime.py`: `_store_raw` (insert, status `pending`), `_mark_raw` (`projected` / `rejected`) | `service_id`, `deploy_seq`, `captured_at`, `received_at`, `complete`, `samples[]`, `status`, `scan_id` | `(service_id, scan_id)`; **TTL** `captured_at`, 90 days |
| `review_responses` | Audit log of the PR-gate Stage 3 model call | `services/pr_gate_engine.py`: `stage3` (`insert_one`, skipped if `store_response=False`) | `service_id`, `pr_number`, `dep_name`, `model`, `prompt`, `response`, `responded_at` | `(service_id, pr_number)`; **TTL** `responded_at`, 30 days |

Note: the older global unique index on `sha256` alone is dropped by `ensure_indexes()`, because it
lost a second service's identical upload. Uniqueness is now per service. Live check:
`raw_manifests` has indexes `service_id_1_uploaded_at_-1` and `service_id_1_sha256_1`;
`runtime_samples` has `captured_at_1` with `expireAfterSeconds` 7776000 (90 days);
`package_embeddings` has `package_id_1` (unique) and `cluster_id_1`.

Why a document here and not rows:

- `raw_manifests`: file text and parsed snapshot are written once and replayed whole. Postgres
  keeps the normalised result (`package`, `resolves_to`, ...).
- `osv_advisories`: OSV fields vary by advisory; Postgres keeps only the columns we query
  (`vulnerability`, `affects`, `resolves`).
- `package_embeddings`: a 384-float vector has no relational meaning and is fully recomputed.
- `runtime_samples`: up to 5000 samples per batch, written as one insert; Postgres keeps the
  aggregated `observes` rows.
- `review_responses`: free text, self-contained, auto-expiring. The authoritative verdict is in
  Postgres (`pr_risk_verdict`).

### 4. One-way flow MongoDB to PostgreSQL [implemented]

Postgres rows are only ever derived from Mongo documents, never the other way round.

Manifest ingest (`POST /ingest/manifest`):

1. Check the service exists (Postgres, short transaction).
2. `_store_manifest`: upsert the raw document by `(service_id, sha256)`, set status `received`.
   Identical re-upload reuses the document. If Mongo is down the request fails with 503 and
   Postgres is not touched.
3. `_project` parses the stored `raw_content`, then calls OSV and deps.dev with no database
   transaction open.
4. `osv_fetcher.store_raw(..., required=True)`: advisories are saved to `osv_advisories` before any
   vulnerability row exists in Postgres.
5. One Postgres transaction: per-service advisory lock, packages, metadata, vulnerabilities,
   deployment with `resolves_to` closure, `scan_run`.
6. On commit, `_mark(..., "projected")` records `deploy_seq`, `projected_at`, `parsed_packages`;
   on any exception it records `failed` with `error`. Then the blast-radius cache is invalidated.
7. `POST /ingest/replay/{manifest_id}` loads the stored document and runs step 3 onward again, as a
   new deployment. This rebuilds Postgres from Mongo (422 bad id, 404 unknown, 503 Mongo down).

Runtime samples (`POST /runtime/samples`, `services/runtime.py`):

1. Resolve the service and deployment (404 / 409 on failure).
2. `_store_raw`: insert the batch with status `pending`. Here a Mongo failure is only logged and
   the batch is still projected (`raw_stored: false`), unlike ingest.
3. One Postgres transaction inserts a `scan_run` and upserts `observes` rows (`exec_count`,
   `is_live`). With `complete: true`, resolved packages missing from the batch get `exec_count` 0.
4. The document is marked `projected` with `scan_id`, or `rejected` with `error` on a 404/409.

Other paths: `POST /ingest/scan/{id}` also stores advisories first, but best-effort (a Mongo outage
is logged and the scan continues). Cluster recompute writes Postgres first and Mongo
embeddings after; this is not atomic, and a failed Mongo step heals on the next recompute (noted in
a code comment).

### 5. Redis: key-value model [implemented]

| Key | Type | Value | Expiry |
|---|---|---|---|
| `dephgraph:blast_radius:gen` | string (counter) | generation number; absent means 0 | none |
| `dephgraph:blast_radius:{service_id}:{gen}` | string | JSON blast-radius result | 24 h (`CACHE_TTL = 86400`) |
| `dephgraph:lock:cluster_recompute` | string | random token of the lock holder | 900 s (`LOCK_TTL`) |

Blast-radius cache (`services/blast_radius.py`): a reader reads the generation first, then
`GET`s `{service}:{gen}`; on a miss it computes from Postgres and `SET ... EX 86400`. `invalidate()`
does `INCR` on the generation and deletes old keys. A reader that started before an invalidation
writes to the old generation, which nobody reads, so it cannot bring back stale data.

Recompute lock (`services/cluster_engine.py`): acquired with `SET key token NX EX 900`, so only one
recompute runs at a time. Release is a Lua script: delete only if the stored token is still ours,
so a run that outlives its TTL cannot free a newer holder's lock.

Live check:

```
$ docker exec dephgraph-redis-1 redis-cli --scan --pattern 'dephgraph:*' | head
dephgraph:blast_radius:1:0
$ docker exec dephgraph-redis-1 redis-cli ttl dephgraph:blast_radius:1:0
82426
$ docker exec dephgraph-redis-1 redis-cli exists dephgraph:blast_radius:gen
0
```

`:0` means generation 0 (no invalidation yet, so the gen key does not exist). The TTL counts down
from 86400 seconds. The value decodes to keys `service_id`, `vulnerable_packages` (6 entries),
`total_vulns`, `critical_count`, `cached`.

### 6. Sample documents

Fetched from the local stack on 22 Nov 2026, after one real `POST /ingest/manifest` (the
`tests/fixtures/requirements_flask.txt` manifest for service 1, which pulled 43 live advisories from
OSV.dev), one `POST /runtime/samples` batch, one `POST /ingest/replay/{id}` and one saved PR-gate run
with the Groq stage on (`scripts/pr_gate_dry_run.py --save`). Long fields are trimmed with `…`.

`raw_manifests` (`raw_content` omitted; 2 of 8 parsed packages shown). `deploy_seqs: [3, 4]` shows the
original projection (deployment 3) and the replay (deployment 4) of the same stored document:

```js
{
  _id: ObjectId('6b02c1fe6b98a12bf00aa39f'), service_id: 1,
  sha256: 'b8deabe27372bb0dc0da25fdadf6190c0e01fe1daf349e157ec08089541e65c7',
  ecosystem: 'pypi', filename: 'requirements.txt',
  uploaded_at: ISODate('2026-11-22T10:20:45.974Z'), received_at: ISODate('2026-11-22T10:20:45.974Z'),
  status: 'projected', projected_at: ISODate('2026-11-22T10:21:00.512Z'),
  deploy_seq: 4, deploy_seqs: [ 3, 4 ],
  parsed_packages: [ { name: 'flask', version: '2.0.1', is_direct: true },
                     { name: 'werkzeug', version: '2.0.1', is_direct: true }, … ]
}
```

`osv_advisories` (the verbatim OSV JSON is kept under `raw_osv`; a few fields shown):

```js
{
  osv_id: 'GHSA-29vq-49wr-vm6x', fetched_at: ISODate('2026-11-22T10:21:00.453Z'),
  raw_osv: {
    id: 'GHSA-29vq-49wr-vm6x',
    summary: 'Werkzeug safe_join() allows Windows special device names',
    aliases: [ 'CVE-2026-27199', 'PYSEC-2026-2320' ],
    severity: [ { type: 'CVSS_V4', score: 'CVSS:4.0/AV:N/AC:L/AT:P/PR:N/UI:N/VC:N/VI:N/VA:L/SC:N/SI:N/SA:N' } ],
    affected: [ { package: { name: 'werkzeug', ecosystem: 'PyPI' },
                  ranges: [ { type: 'ECOSYSTEM', events: [ { introduced: '0' }, { fixed: '3.1.6' } ] } ] } ],
    …
  }
}
```

`runtime_samples` (2 of 4 samples shown). The batch reported 8 `observes` rows written (`complete:
true` adds `exec_count` 0 rows for resolved packages not in the batch), 2 live packages, and 1 sample
skipped because its name did not match a resolved package:

```js
{
  _id: ObjectId('6b02c20abce8fbe8465937d6'), service_id: 1, deploy_seq: 3,
  captured_at: ISODate('2026-11-22T10:20:58.015Z'), received_at: ISODate('2026-11-22T10:20:58.015Z'),
  complete: true,
  samples: [ { name: 'flask', version: '2.0.1', exec_count: 100 },
             { name: 'werkzeug', version: '2.0.1', exec_count: 200 }, … ],
  status: 'projected', scan_id: 8, projected_at: ISODate('2026-11-22T10:20:58.030Z')
}
```

`package_embeddings` (vector truncated; real length 384):

```js
{ package_id: 2, cluster_id: 50, embedding: [ … 384 floats … ], umap_2d: [ 5.9729, 7.3226 ] }
```

`review_responses` (written by `stage3` for each ADVISORY dependency the LLM judged; prompt trimmed):

```js
{
  service_id: 1, pr_number: 9001, dep_name: 'werkzeug', model: 'openai/gpt-oss-120b',
  prompt: 'Dependency werkzeug==2.0.1 added in a PR. Rule-based verdict: ADVISORY (abandonment score 55 in 40-79; CVSS 6.30 in 6.0-8.9; CVSS 7.50 in 6.0-8.9; …',
  response: '{"verdict":"ADVISORY","reasoning":"The dependency has multiple medium-severity CVEs and an abandonment score of 55, indicating security risk, but no copyleft restrictions; thus an advisory is appropriate."}',
  responded_at: ISODate('2026-11-22T10:38:52.880Z')
}
```

### 7. Aggregation pipelines run on live data [implemented]

Raw manifests by status and ecosystem:

```js
db.raw_manifests.aggregate([
  { $group: { _id: { status: "$status", ecosystem: "$ecosystem" }, n: { $sum: 1 } } }
])
// [ { _id: { status: 'projected', ecosystem: 'pypi' }, n: 1 } ]
```

Largest semantic clusters by number of packages, from `package_embeddings`:

```js
db.package_embeddings.aggregate([
  { $match: { cluster_id: { $ne: null } } },
  { $group: { _id: "$cluster_id", packages: { $sum: 1 } } },
  { $sort: { packages: -1, _id: 1 } },
  { $limit: 3 }
])
// [ { _id: 55, packages: 17 }, { _id: 57, packages: 16 }, { _id: 54, packages: 13 } ]
```

Cluster 55 is "web / requests / http", 57 "data / serialization / yaml", 54 "database / databases /
sql". The collection holds 97 documents (only packages with a description are embedded); 88 have a
`cluster_id` and 9 are HDBSCAN noise (`null`). Collection sizes: `raw_manifests` 1,
`osv_advisories` 43, `runtime_samples` 1, `review_responses` 1.

### 8. Replication, consistency and CAP [design]

The Compose stack runs one standalone MongoDB and one Redis, so nothing here is deployed.

- **Primary/secondary:** a MongoDB replica set has one primary taking writes; secondaries copy its
  oplog. If the primary fails, an election picks a new one. Atlas M0 (the planned target in
  `docs/DEPLOY.md`) is a managed replica set.
- **Consistency trade-off:** reading from a secondary can return data a little behind the primary
  (eventual consistency). DephGraph's raw-first write (step 2 above) should use write concern
  `majority` and read from the primary, because step 3 reads back what step 2 wrote. Reads of
  `package_embeddings` for the cluster map could tolerate secondaries.
- **CAP:** under a network partition a system picks consistency or availability. A default MongoDB
  replica set favours consistency: the minority side cannot elect a primary and refuses writes. A
  cache like Redis is used the other way: if it is unreachable the code logs and falls through to
  Postgres (`cache_read_failed`), so the dashboard stays available with stale-free but slower data.
  Postgres stays the single source of truth in both cases.
- **Redis:** replication and failover are provider settings (Upstash), not application code.

### 9. Sharding [design]

Sharding splits a collection across servers by a shard key, and each shard is normally itself a
replica set. The current data volume does not need it. If it grew:

- `raw_manifests`: shard key `service_id` (hashed) fits the dominant access pattern, which is
  per-service, and matches the unique index `(service_id, sha256)` (a unique index on a sharded
  collection must contain the shard key). Risk: one very large service is a hot shard.
- `package_embeddings`: key `package_id`; the cluster job reads all documents, so it becomes a
  scatter-gather across shards.
- `osv_advisories`: key `osv_id`, looked up by exact id, so it targets a single shard.
- Redis: key-based partitioning by service id would work, but the generation counter would then
  need to stay on one node.

### 10. Big data and MapReduce [design]

Volume, velocity and variety map to DephGraph as: millions of package versions across ecosystems,
frequent advisory updates, and mixed structured / semi-structured / text data. Nothing here uses
Hadoop; the app runs Postgres queries and Python services.

MapReduce idea applied to DephGraph: to count vulnerable services per package across many
manifests, the map step reads one manifest and emits `(package@version, service_id)`, the shuffle
groups by key, and the reduce step counts distinct services per key. The equivalent in this
project is the SQL `GROUP BY` and the Mongo `$group` stage in section 7, which are the same
group-and-reduce pattern on a single node.

### 11. Data types and scale

- **Structured:** normalised Postgres entities, keys, dependency edges, vulnerability links.
- **Semi-structured:** manifest JSON/text and OSV advisories.
- **Unstructured / vector:** package descriptions, model prompt and response text, 384-dimension
  embeddings.

## Viva checkpoints

- **Why is `OBSERVES` ternary?** The execution count belongs to a package version in a deployment
  during one particular scan window. Splitting it loses the association among all three.
- **Why carry the full version key in `AFFECTS`?** `package_version` is identified by
  `(package_id, version_string)`; package id alone cannot identify the affected release.
- **Why not use basic relational algebra for arbitrary blast radius?** Basic relational algebra
  has no unbounded transitive-closure operator. The application uses a recursive CTE; the course
  sample also stores resolved closure rows.
- **Is the coursework schema the product schema?** They model the same domain but are separate
  schemas with documented differences; the ER model is the coursework design, not an exact product
  migration.

## Submission evidence checklist

- ER diagram and MySQL Workbench EER screenshot.
- `sql/queries.sql` (Q1–Q7) and `sql/complex_queries.sql` output.
- `sql/constraint_demos.sql` and transaction rollback output.
- Two-session lock-wait/deadlock screenshots or captured transcripts, plus a precedence graph for
  each schedule.
- Dashboard screenshots from the already-built product.
- Final report/synopsis updated to distinguish course and product schemas and summarize phases 1–6.
