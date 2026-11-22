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

The code currently configures indexes for five MongoDB collection names in
`apps/api/app/models/mongo.py`. Four have active application write paths; `runtime_samples` has
indexes and a TTL policy but no writer was found in the current API/services. It should therefore
be described as a prepared/unused collection, not as live telemetry.

| Store / collection | Current role and natural aggregate |
|---|---|
| MongoDB `raw_manifests` | One uploaded manifest document, keyed for deduplication by `(service_id, sha256)`, containing raw text and parsed-package snapshot. |
| MongoDB `osv_advisories` | One advisory document per `osv_id`, keeping the verbatim upstream OSV payload together with its fetch timestamp. |
| MongoDB `package_embeddings` | One computed embedding/UMAP point per package, with cluster id; recomputation replaces this derived document. |
| MongoDB `review_responses` | One Stage 3 model response for a PR dependency, including prompt, response, model, and timestamp. It is best-effort audit/context data, not the authoritative verdict. |
| MongoDB `runtime_samples` | Collection with service/scan and TTL indexes (90 days); no active writer found in the current code. |
| Redis | A blast-radius result cached as JSON under a service id and generation key, with a 24-hour TTL; invalidation advances the generation and deletes old keys. |

These Mongo documents are useful as aggregates because each is usually fetched or replaced as a
whole: raw source payloads retain flexible upstream fields, embeddings are recomputable derived
vectors, and review responses preserve a self-contained request/response record. Relational data
such as service ownership, package versions, vulnerabilities, and verdicts remains in PostgreSQL
because it needs constraints, joins, and transactional updates.

### Distribution concepts (not deployment claims)

The repository configures MongoDB and Redis clients but does not prove that a production Atlas or
Upstash deployment exists. A MongoDB replica set keeps copies of the same shard/collection data on
multiple members for availability; a sharded cluster partitions data across shards and normally
replicates each shard. A sharding key should follow dominant access patterns—for example service id
for service-scoped uploads, or package id for package embeddings—and should account for hot keys
and cross-shard queries. Redis replication/failover is a provider/runtime configuration, not
something guaranteed by the application code. “Primary/replica” describes a common replication
topology; peer-to-peer or multi-primary behavior must not be assumed without selecting and
configuring a product that supports it.

### Data types and scaling

- **Structured:** normalized PostgreSQL entities, keys, dependency edges, and vulnerability links.
- **Semi-structured:** manifest JSON and OSV advisory documents, whose upstream fields can vary.
- **Unstructured / vector-like:** package descriptions and model prompt/response text; embeddings
  are numerical vectors used for similarity and clustering.
- **At larger scale:** partitioned batch jobs could aggregate advisory exposure or recompute
  embeddings. Hadoop/MapReduce is a possible historical/distributed-batch architecture, not a
  current DephGraph dependency; the product currently uses PostgreSQL queries and application
  services.

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
