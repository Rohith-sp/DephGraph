# DephGraph — Phase 4: SQL Implementation

Phases 1 to 3 designed the database on paper:
the ER model ([`../er-diagram/`](../er-diagram/README.md)),
the 23 relations ([`../relational-model/`](../relational-model/README.md)),
the normalization proof ([`../normalization/`](../normalization/README.md)) and
the constraints and relational algebra ([`../relational-algebra/`](../relational-algebra/README.md)).

Phase 4 builds that design in **MySQL 8.4** and runs it. It covers **Unit III** of the syllabus
(SQL: DDL, constraints, DML, complex queries):

1. **DDL:** create the 23 tables, 26 foreign keys, 9 CHECKs and 3 views (`er-diagram/schema.sql`)
2. **DML:** load the Phase 2 sample rows (`seed.sql`)
3. **Constraints:** run the 12 violation cases from Phase 3b and see MySQL's real answer (`constraint_demos.sql`)
4. **Relational algebra in SQL:** Q1–Q7 from Phase 3b, with the same results as the hand-computed ones (`queries.sql`)
5. **More complex SQL:** subqueries, outer joins, NULL logic, aggregates, views, recursion
   (`seed_extra.sql` + `complex_queries.sql`)

Every output in this document is pasted from a real run (MySQL 8.4.13 in Docker, 21 Nov 2026).

---

## Files and run order

| # | File | What it does | Errors expected? |
|---|---|---|---|
| 1 | [`../er-diagram/schema.sql`](../er-diagram/schema.sql) | drops and re-creates database `dephgraph`: 23 tables, 3 views | no |
| 2 | [`seed.sql`](seed.sql) | empties every table, loads exactly the Phase 2 sample rows (log4j / Log4Shell story) | no |
| 3 | [`constraint_demos.sql`](constraint_demos.sql) | the 12 Phase 3b cases + X1, X2; each one rolled back | **yes, 10** |
| 4 | [`queries.sql`](queries.sql) | Q1–Q7 in SQL, on the base sample only | no |
| 5 | [`seed_extra.sql`](seed_extra.sql) | adds more rows (new IDs only) so complex queries have something to find | no |
| 6 | [`complex_queries.sql`](complex_queries.sql) | 25 "more complex SQL" queries (C1–C25), on base + extra rows | no |

The order matters: `queries.sql` must run **before** `seed_extra.sql`, because the hand-computed
results of Phase 3b are for the small sample only.

Run everything from the repository root (MySQL runs in the Docker container `dephgraph-mysql`; start it
with `scripts/mysql.sh up`):

```bash
scripts/mysql.sh run er-diagram/schema.sql
scripts/mysql.sh run sql/seed.sql
scripts/mysql.sh run --force sql/constraint_demos.sql   # --force: keep going after the expected errors
scripts/mysql.sh run sql/queries.sql
scripts/mysql.sh run sql/seed_extra.sql
scripts/mysql.sh run sql/complex_queries.sql
```

Each run saves its output to `logs/<file>.log` (not in Git). In the last full run, every file ended
with `exit code: 0` and had **0 warnings**; only `constraint_demos.sql` had errors, and all 10 were
the expected ones.

To go back to the plain sample data afterwards, run steps 1 and 2 again.

### How the SQL files are written

Each query is preceded by a **label row**, so the log can be read on its own:

```sql
SELECT 'Q2a: deployed but never executed, with EXCEPT (set difference)' AS query,
       '{(10, spring-boot-starter-log4j2, 2.5.0), (10, logback-classic, 1.2.3)}' AS expected;
SELECT ...;   -- the real query; its result is printed right under the label
```

Anything that changes data (Q6c, C22, all constraint demos) runs inside
`START TRANSACTION … ROLLBACK`, so the scripts leave the data as they found it.

---

## 1. DDL fixes made in Phase 4

`schema.sql` was checked against the Phase 2 and Phase 3 documents. Two things were fixed:

| Fix | Before | After | Why |
|---|---|---|---|
| `depends_on` primary key | 5 columns, included `scope` | 4 columns: `(dependent_package_id, dependent_version, dependency_package_id, dependency_version)` | `scope` is a non-key attribute: one dependency edge has one scope. This is the key in the relational model and normalization documents. |
| `CHECK chk_exec_count` on `observes` | missing | `CHECK (exec_count >= 0)` | domain rule from Phase 3b §1.1 |
| `CHECK chk_depth` on `resolves_to` | missing | `CHECK (depth >= 0)` | domain rule from Phase 3b §1.1 (0 = direct dependency) |

After the fixes the schema has **23 tables, 26 foreign keys, 9 CHECK constraints and 3 views**, and
loads with no warnings.

---

## 2. Constraint violations on MySQL

[`constraint_demos.sql`](constraint_demos.sql) runs every case from
[Phase 3b, Part 2](../relational-algebra/README.md#part-2--update-operations-and-constraint-violations)
on the sample data. Every case gives the result the document predicted.

| # | Operation | Constraint | MySQL result |
|---|---|---|---|
| I1 | INSERT service for team 9 | referential integrity | `ERROR 1452: Cannot add or update a child row: a foreign key constraint fails (… fk_service_team …)` |
| I2 | INSERT deployment with service_id NULL | entity integrity | `ERROR 1048: Column 'service_id' cannot be null` |
| I3 | INSERT a second team 'Payments' | key (UNIQUE team_name) | `ERROR 1062: Duplicate entry 'Payments' for key 'team.team_name'` |
| I4 | INSERT vulnerability with cvss_score 11.5 | domain | `ERROR 3819: Check constraint 'chk_cvss' is violated.` |
| I5 | INSERT log4j-core 2.14.1 depending on itself | semantic | `ERROR 3819: Check constraint 'chk_no_self_dep' is violated.` |
| I6 | INSERT package gson with cluster_id NULL | none (partial participation) | accepted: row `(103, gson, Maven, NULL)` |
| D1 | DELETE team 1 | ON DELETE RESTRICT | `ERROR 1451: Cannot delete or update a parent row: a foreign key constraint fails (… fk_plan_team …)` |
| D2 | DELETE service 10 | ON DELETE CASCADE | accepted; 2 + 3 + 2 + 1 + 2 + 1 = **11 rows** cascade (deployment, resolves_to, observes, pr_review, pr_risk_verdict, waived_for all go from those counts to 0) |
| D3 | DELETE licence 2 | ON DELETE SET NULL | accepted: `(101, 1.2.3)` stays with `license_id = NULL` |
| U1 | UPDATE service 11 to team 9 | referential integrity | `ERROR 1452: … fk_service_team …` |
| U2 | UPDATE key of log4j-core 2.14.1 | referenced key, no ON UPDATE CASCADE | `ERROR 1451: … fk_aff_version …` |
| U3 | UPDATE cvss_score of 901 to 9.8 | none | accepted: `cvss_score = 9.8` |
| X1 | INSERT observation with exec_count −1 | domain (new CHECK) | `ERROR 3819: Check constraint 'chk_exec_count' is violated.` |
| X2 | UPDATE resolved depth to −1 | domain (new CHECK) | `ERROR 3819: Check constraint 'chk_depth' is violated.` |

10 errors in total, all expected. The last check in the script shows the data is unchanged
(2 teams, 2 services, 2 deployments, 3 packages, 4 versions, 2 vulnerabilities, cvss of 901 = 9.0).

For D1, MySQL reports only the first foreign key it finds blocking the delete (`fk_plan_team`, from
plan 50). Service 10 blocks it too, through `fk_service_team`.

---

## 3. Q1–Q7: relational algebra in SQL

[`queries.sql`](queries.sql) translates the seven queries of
[Phase 3b, Part 3](../relational-algebra/README.md#part-3--relational-algebra). They run on the
base sample data only.

How the RA operators map to SQL:

| RA | SQL |
|---|---|
| σ condition | `WHERE` |
| π attributes | `SELECT DISTINCT` (RA removes duplicates, SQL does not unless asked) |
| ⋈ natural join | `JOIN … ON` the FK = PK columns |
| ρ rename | table alias (`AS sp2`) or column alias |
| ∪, ∩, − | `UNION`, `INTERSECT`, `EXCEPT` (all three exist in MySQL since 8.0.31) |
| ℱ aggregate | `GROUP BY` with `COUNT`, `SUM`, … |
| ÷ division | no operator; double `NOT EXISTS` or `GROUP BY … HAVING COUNT(*) = …` |

### Q1 — Blast radius

**Q1a**, the direct translation (RESOLVES_TO already holds every version a deployment reaches):

```sql
SELECT DISTINCT s.service_name
FROM   vulnerability v
JOIN   affects       a  ON a.vuln_id = v.vuln_id
JOIN   resolves_to   rt ON rt.package_id = a.package_id AND rt.version_string = a.version_string
JOIN   deployment    d  ON d.service_id = rt.service_id AND d.deploy_seq = rt.deploy_seq
JOIN   service       s  ON s.service_id = d.service_id
WHERE  v.osv_id = 'GHSA-jfh8-c2jp-5v3q'
  AND  d.environment = 'PROD' AND d.is_active = TRUE;
```
```
+--------------+
| service_name |
+--------------+
| payments-svc |
+--------------+
```

**Q1b**, with `WITH RECURSIVE`. Phase 3b explains that basic relational algebra **cannot express
transitive closure**: every extra level of DEPENDS_ON needs one more join, and a chain can be any
length. SQL:1999 added recursive common table expressions. The recursive part (one join with
`depends_on`) is repeated until it adds no new rows, so it computes LEVEL0 ∪ LEVEL1 ∪ LEVEL2 ∪ …
for a chain of any depth:

```sql
WITH RECURSIVE reaches (package_id, version_string, level, path) AS (
    SELECT a.package_id, a.version_string, 0, CAST(CONCAT(p.package_name,'@',a.version_string) AS CHAR(1000))
    FROM   affects a JOIN vulnerability v ON v.vuln_id = a.vuln_id
                     JOIN package p       ON p.package_id = a.package_id
    WHERE  v.osv_id = 'GHSA-jfh8-c2jp-5v3q'                 -- anchor = LEVEL0
    UNION ALL
    SELECT d.dependent_package_id, d.dependent_version, r.level + 1,
           CONCAT(p.package_name,'@',d.dependent_version,' -> ',r.path)
    FROM   reaches r
    JOIN   depends_on d ON d.dependency_package_id = r.package_id
                       AND d.dependency_version    = r.version_string
    JOIN   package p    ON p.package_id = d.dependent_package_id
    WHERE  r.level < 20                                      -- LEVEL(n+1); guard against cycles
)
SELECT level, package_id, version_string, path FROM reaches ORDER BY level, package_id;
```
```
+-------+------------+----------------+-------------------------------------------------------+
| level | package_id | version_string | path                                                  |
+-------+------------+----------------+-------------------------------------------------------+
|     0 |        100 | 2.14.1         | log4j-core@2.14.1                                     |
|     1 |        102 | 2.5.0          | spring-boot-starter-log4j2@2.5.0 -> log4j-core@2.14.1 |
+-------+------------+----------------+-------------------------------------------------------+
```

This is LEVEL0 = {(100, 2.14.1)}, LEVEL1 = {(102, 2.5.0)} and LEVEL2 = ∅, as in the RA document.
Joining this set with only the **direct** dependencies (`resolves_to.depth = 0`) finds the service
without using the stored closure at all:

```
+--------------+-------------+-----------------------+-------------------------------------------------------+
| service_name | environment | hops_below_direct_dep | path                                                  |
+--------------+-------------+-----------------------+-------------------------------------------------------+
| payments-svc | PROD        |                     1 | spring-boot-starter-log4j2@2.5.0 -> log4j-core@2.14.1 |
+--------------+-------------+-----------------------+-------------------------------------------------------+
```

**Matches RA doc: yes** (Q1 = {payments-svc}; LEVEL1 and LEVEL2 also match).

### Q2 — Prune: deployed but never executed

Set difference, written two ways:

```sql
-- Q2a: EXCEPT
SELECT dead.service_id, p.package_name, dead.version_string
FROM ( SELECT service_id, deploy_seq, package_id, version_string FROM resolves_to          -- RES
       EXCEPT
       SELECT service_id, deploy_seq, package_id, version_string FROM observes
       WHERE  exec_count > 0 ) AS dead                                                    -- LIVE
JOIN package p ON p.package_id = dead.package_id;

-- Q2b: NOT EXISTS (works on any SQL database)
SELECT rt.service_id, p.package_name, rt.version_string
FROM   resolves_to rt JOIN package p ON p.package_id = rt.package_id
WHERE  NOT EXISTS (SELECT 1 FROM observes o
                   WHERE o.service_id = rt.service_id AND o.deploy_seq = rt.deploy_seq
                     AND o.package_id = rt.package_id AND o.version_string = rt.version_string
                     AND o.exec_count > 0);
```
Both print:
```
+------------+----------------------------+----------------+
| service_id | package_name               | version_string |
+------------+----------------------------+----------------+
|         10 | spring-boot-starter-log4j2 | 2.5.0          |
|         10 | logback-classic            | 1.2.3          |
+------------+----------------------------+----------------+
```
**Matches RA doc: yes.**

### Q3 — Minimum remediation (aggregate ℱ → GROUP BY)

```sql
SELECT rt.package_id, rt.version_string,
       COUNT(DISTINCT rt.service_id) AS services,
       COUNT(DISTINCT a.vuln_id)     AS vulns
FROM   deployment  d
JOIN   resolves_to rt ON rt.service_id = d.service_id AND rt.deploy_seq = d.deploy_seq
JOIN   affects     a  ON a.package_id = rt.package_id AND a.version_string = rt.version_string
WHERE  d.environment = 'PROD' AND d.is_active = TRUE
GROUP  BY rt.package_id, rt.version_string
ORDER  BY services DESC, vulns DESC;
```
```
+------------+----------------+----------+-------+
| package_id | version_string | services | vulns |
+------------+----------------+----------+-------+
|        100 | 2.14.1         |        1 |     2 |
+------------+----------------+----------+-------+
```
**Matches RA doc: yes** (one round of greedy set cover: upgrading log4j-core 2.14.1 fixes 1 service and 2 vulnerabilities).

### Q4 — Licence propagation (multi-way join)

```sql
SELECT DISTINCT s.service_name, p.package_name, pv.version_string, l.spdx_id
FROM   license l
JOIN   package_version pv ON pv.license_id = l.license_id
JOIN   resolves_to rt     ON rt.package_id = pv.package_id AND rt.version_string = pv.version_string
JOIN   service s          ON s.service_id = rt.service_id
JOIN   package p          ON p.package_id = pv.package_id
WHERE  l.is_copyleft = TRUE;
```
```
+--------------+-----------------+----------------+---------------+
| service_name | package_name    | version_string | spdx_id       |
+--------------+-----------------+----------------+---------------+
| payments-svc | logback-classic | 1.2.3          | LGPL-2.1-only |
+--------------+-----------------+----------------+---------------+
```
**Matches RA doc: yes.**

### Q5 — Consolidate (self-join)

SP and its renamed copy SP2 become two aliases of the same derived table:

```sql
SELECT DISTINCT s.service_name, sc.cluster_label
FROM  (SELECT DISTINCT rt.service_id, p.package_id, p.cluster_id
       FROM resolves_to rt JOIN package p ON p.package_id = rt.package_id
       WHERE p.cluster_id IS NOT NULL) AS sp1
JOIN  (… same subquery …) AS sp2
      ON  sp2.service_id = sp1.service_id
      AND sp2.cluster_id = sp1.cluster_id
      AND sp1.package_id < sp2.package_id        -- different packages, each pair once
JOIN  service s           ON s.service_id = sp1.service_id
JOIN  semantic_cluster sc ON sc.cluster_id = sp1.cluster_id;
```
```
+--------------+---------------+
| service_name | cluster_label |
+--------------+---------------+
| payments-svc | logging       |
+--------------+---------------+
```
**Matches RA doc: yes.**

### Q6 — Division: maintainers of every logging package

SQL has no ÷. Two standard ways:

```sql
-- Q6a: double NOT EXISTS ("there is no logging package this maintainer does not maintain")
SELECT m.handle
FROM   maintainer m
WHERE  EXISTS (SELECT 1 FROM maintained_by mb WHERE mb.maintainer_id = m.maintainer_id)
  AND  NOT EXISTS (
         SELECT 1 FROM package p JOIN semantic_cluster sc ON sc.cluster_id = p.cluster_id
         WHERE  sc.cluster_label = 'logging'
           AND  NOT EXISTS (SELECT 1 FROM maintained_by mb
                            WHERE mb.maintainer_id = m.maintainer_id
                              AND mb.package_id    = p.package_id));

-- Q6b: count the logging packages each maintainer has, keep those with all of them
SELECT m.handle
FROM   maintained_by mb
JOIN   package p           ON p.package_id = mb.package_id
JOIN   semantic_cluster sc ON sc.cluster_id = p.cluster_id
JOIN   maintainer m        ON m.maintainer_id = mb.maintainer_id
WHERE  sc.cluster_label = 'logging'
GROUP  BY m.maintainer_id, m.handle
HAVING COUNT(DISTINCT mb.package_id) = (SELECT COUNT(*) FROM package p2
                                        JOIN semantic_cluster sc2 ON sc2.cluster_id = p2.cluster_id
                                        WHERE sc2.cluster_label = 'logging');
```

An empty result prints nothing in the MySQL client, so in the file each query is wrapped in a
count. Both print:
```
+---------------+---------+
| rows_returned | handles |
+---------------+---------+
|             0 | -       |
+---------------+---------+
```
Then, inside `START TRANSACTION … ROLLBACK`, the row MAINTAINED_BY (101, 200) is added (mdev also
maintains logback-classic) and both queries run again:
```
+-------------------+--------+
| method            | handle |
+-------------------+--------+
| double NOT EXISTS | mdev   |
+-------------------+--------+
+---------------------+--------+
| method              | handle |
+---------------------+--------+
| GROUP BY ... HAVING | mdev   |
+---------------------+--------+
```
After the rollback, `maintained_by` has 2 rows again.
**Matches RA doc: yes** (∅, and {mdev} with the extra row).

### Q7 — Intersection, difference and union

The waiver date is the fixed `'2026-11-02'` used in the RA document, not `CURDATE()`, so the result
does not change when the waiver expires.

```sql
-- Q7a: AFF ∩ WAIVED
SELECT a.vuln_id, rt.service_id
FROM   resolves_to rt JOIN affects a ON a.package_id = rt.package_id AND a.version_string = rt.version_string
INTERSECT
SELECT vuln_id, service_id FROM waived_for WHERE expires_on >= '2026-11-02';

-- Q7b: AFF − WAIVED  (same, with EXCEPT)

-- Q7c: copyleft ∪ vulnerable
SELECT pv.package_id FROM package_version pv JOIN license l ON l.license_id = pv.license_id
WHERE  l.is_copyleft = TRUE
UNION
SELECT package_id FROM affects
ORDER  BY package_id;
```
Q7a, Q7b and Q7c print:
```
+---------+------------+
| vuln_id | service_id |
+---------+------------+
|     901 |         10 |
+---------+------------+
+---------+------------+
| vuln_id | service_id |
+---------+------------+
|     900 |         10 |
+---------+------------+
+------------+
| package_id |
+------------+
|        100 |
|        101 |
+------------+
```
**Matches RA doc: yes** (Q7a = {(901, 10)}, Q7b = {(900, 10)}, Q7c = {100, 101}).

### Summary

| Query | SQL features | Result | Matches RA doc |
|---|---|---|---|
| Q1 | joins; `WITH RECURSIVE` | {payments-svc} | yes |
| Q2 | `EXCEPT`; `NOT EXISTS` | 2 rows | yes |
| Q3 | `GROUP BY`, `COUNT(DISTINCT)` | (100, 2.14.1, 1, 2) | yes |
| Q4 | 5-table join | 1 row | yes |
| Q5 | self-join | (payments-svc, logging) | yes |
| Q6 | double `NOT EXISTS`; `HAVING COUNT = …`; transaction | ∅, then {mdev} | yes |
| Q7 | `INTERSECT`, `EXCEPT`, `UNION` | as documented | yes |

---

## 4. Extra sample data

The Phase 2 sample is small on purpose (it is checked by hand). [`seed_extra.sql`](seed_extra.sql)
adds rows so that the complex queries find something. It only **adds** rows with new IDs; no base row
is changed. It loads in one transaction and ends with a row-count check (all `ok`).

| What | Added |
|---|---|
| Teams, services | Data, Mobile (owns nothing); reports-svc, web-frontend, legacy-batch (never deployed) |
| Deployments | auth-svc PROD + DEV, reports-svc PROD, web-frontend PROD + an old inactive STAGING |
| Packages | 19 more: Maven (Spring Boot, Jackson, SnakeYAML, json-smart, internal payments-common), npm (express, body-parser, qs, axios, node-fetch, lodash), PyPI (requests, urllib3, pyyaml, mysql-connector-python), Go (golang.org/x/text) |
| Dependency chains | `spring-boot-starter-web → spring-boot-starter-json → jackson-databind → jackson-core` (3 levels); `express → body-parser → qs` and `express → qs` (a diamond: two paths) |
| Vulnerabilities | 12 more, LOW to CRITICAL; one (DG-2026-0001) has **no CVSS score** (NULL); one (GHSA-p6xc-xr62-6r2g) affects no version in the database |
| Licences | MIT, GPL-2.0-only, BSD-3-Clause, MPL-2.0 (used by nothing) |
| Clusters | "json parsing", "yaml parsing"; "http client" gets axios, node-fetch, requests. Cluster "http client" still has `representative_package_id = NULL` |
| Other | 10 maintainers (one with unknown activity date), 2 waivers (one already expired on 2026-10-31), 3 plans, 5 actions, 3 PRs, 4 verdicts (one for `left-pad`, which is not in the catalogue) |

Totals after both seeds: 4 teams, 5 services, 7 deployments, 22 packages, 26 versions, 14
vulnerabilities, 24 resolves_to rows, 11 depends_on edges.

---

## 5. More complex SQL

[`complex_queries.sql`](complex_queries.sql) has 25 queries (C1–C25) on base + extra data. "Today"
is fixed as `@as_of = '2026-11-21'` so results stay the same; only the three existing views use
`CURDATE()`.

| # | Question | SQL concept | Result (actual) |
|---|---|---|---|
| C1 | Which vulnerabilities score above the average CVSS? | nested subquery; `AVG` ignores NULL | 8 rows, avg = 7.19: Log4Shell 10.0, PyYAML 9.8, 901 9.0, SnakeYAML 8.3, three 7.5s, lodash 7.2 |
| C2 | Which services run a CRITICAL vulnerability in live PROD? | `IN`, 3 levels of nesting, row constructor `(a, b) IN (…)` | payments-svc, reports-svc |
| C3 | Which packages are used by no deployment? | `NOT IN` | json-smart, golang.org/x/text |
| C4 | How does SQL compare with NULL? | three-valued logic | `NULL = NULL` → NULL, `7 NOT IN (5, NULL)` → NULL, `NULL AND FALSE` → 0, `NULL OR TRUE` → 1 |
| C5 | Do "cvss ≥ 7" and "NOT (cvss ≥ 7)" add up to all vulnerabilities? | UNKNOWN is neither TRUE nor FALSE | total 14 = 8 + 5 + **1 with NULL score** (DG-2026-0001) |
| C6a | Which packages are not a cluster representative? (`NOT IN`) | **NOT IN pitfall** | **0 rows**, because one representative is NULL |
| C6b | Same, done right | `NOT IN … IS NOT NULL`; `NOT EXISTS` | 19 rows both ways |
| C7 | Which deployed versions are older than the newest one we know? | correlated subquery | jackson-databind 2.13.2 (newest 2.15.2), lodash 4.17.20, log4j-core 2.14.1, snakeyaml 1.29 |
| C8 | How many packages does each service ship to PROD? | correlated scalar subquery in `SELECT` | auth-svc 7, web-frontend 6, reports-svc 4, payments-svc 3, legacy-batch 0 |
| C9 | Which teams own a service with an open vulnerability in PROD? | `EXISTS` with `NOT EXISTS` inside | Data, Payments, Platform (not Mobile) |
| C10 | Which plans have no actions; which services were never deployed? | `NOT EXISTS` anti-join | plan 51 (DRAFT); legacy-batch |
| C11a | Which vulnerability has the highest CVSS? (`>= ALL`) | **ALL pitfall** with a NULL | **0 rows** |
| C11b | Same, NULL filtered out | `>= ALL` | GHSA-jfh8-c2jp-5v3q, 10.0 |
| C12 | Which vulnerabilities are worse than at least one log4j advisory? | `> ANY` (= `> SOME`) | Log4Shell 10.0, PyYAML 9.8 (i.e. > 9.0) |
| C13 | How many services does each team own, including none? | `LEFT JOIN`; `COUNT(col)` vs `COUNT(*)` | Payments 2, Platform 2, Data 1, Mobile **0** (but 1 joined row) |
| C14 | Which PR-gate dependencies are not in the catalogue? | `LEFT JOIN … IS NULL` | gson (PR 42), left-pad (PR 101) |
| C15 | How many versions use each licence, including unused? | `RIGHT JOIN` | Apache-2.0 13, MIT 8, BSD-3-Clause 2, GPL-2.0-only 1, LGPL-2.1-only 1, MPL-2.0 **0** |
| C16 | Vulnerabilities ⟗ remediation actions | **FULL OUTER JOIN** emulated: `LEFT JOIN UNION RIGHT JOIN` | 16 rows: 7 matched, 7 vulns with no action (NULL on the right), 2 actions that fix no vuln (REMOVE 50/2, CONSOLIDATE 52/3; NULL on the left) |
| C17 | Per severity: count and CVSS min / avg / max | aggregates; `COUNT(*)` vs `COUNT(col)` | LOW: 2 vulns but only 1 with a score |
| C18 | Which PROD services have ≥ 2 open vulnerabilities, how bad? | `GROUP BY … HAVING`, `CASE` | reports-svc 3 (critical), auth-svc 2 (high), web-frontend 4 (high) |
| C19 | Resolved packages per service per environment | pivot with `SUM(CASE …)` | e.g. auth-svc PROD 7, DEV 1; legacy-batch all 0 |
| C20a–c | The three views of `schema.sql` | querying views | see below |
| C21 | New view `v_prod_inventory`: packages and copyleft per service and ecosystem | `CREATE VIEW` + `GROUP BY` | payments-svc Maven 3 (1 copyleft), reports-svc PyPI 4 (1), auth-svc Maven 7, web-frontend npm 6 |
| C22 | Updatable view `WITH CHECK OPTION` | insert through a view, rolled back | PROD row accepted; DEV row `ERROR 1369: CHECK OPTION failed 'dephgraph.v_prod_deployment'` |
| C23 | Blast radius of every HIGH/CRITICAL vulnerability | `WITH RECURSIVE` (upwards), any depth | 11 paths, see below |
| C24 | Rebuild auth-svc's dependency tree; does it match the stored depth? | `WITH RECURSIVE` (downwards), `MIN` per node | 7 versions, depth 0–3, all `ok` |
| C25 | Top 3 upgrades to do first | `ORDER BY` several keys, `LIMIT` | log4j-core 2.14.1 (10.0), pyyaml 5.3.1 (9.8), snakeyaml 1.29 (8.3) |

### Two NULL pitfalls worth remembering

`x NOT IN (subquery)` is `x <> v1 AND x <> v2 AND …`. If one `v` is NULL, that part is UNKNOWN, so
the whole condition can never be TRUE, and the query returns nothing (C6a). The same happens with
`>= ALL` (C11a). `NOT EXISTS` does not have this problem, because `EXISTS` is only ever TRUE or FALSE.

```
C6a  NOT IN                    0 rows
C6b  NOT IN ... IS NOT NULL   19 rows
     NOT EXISTS               19 rows
```

### Views (C20)

The views `v_service_exposure` and `v_package_abandonment` use `CURDATE()`. Their numbers change
with the date:

- The waiver on vulnerability **901** (GHSA-7rjr-3q55-vv33) for payments-svc **expires on
  2026-11-30**. Until then `v_service_exposure` shows 1 open vulnerability for payments-svc; from
  1 December 2026 it shows 2.
- `days_idle` and `abandonment_score` grow every day.
- `CURDATE()` is the MySQL server's date. The Docker container runs in UTC, so it showed
  2026-11-20 during this run although the local date (IST) was already 21 November.

Output of this run:
```
C20a v_service_exposure
+------------+------------+--------------+------------+----------------+----------+
| run_date   | service_id | service_name | open_vulns | critical_vulns | avg_cvss |
+------------+------------+--------------+------------+----------------+----------+
| 2026-11-20 |         13 | web-frontend |          4 |              0 |     6.63 |
| 2026-11-20 |         12 | reports-svc  |          3 |              1 |     6.27 |
| 2026-11-20 |         11 | auth-svc     |          2 |              0 |     7.90 |
| 2026-11-20 |         10 | payments-svc |          1 |              1 |    10.00 |
+------------+------------+--------------+------------+----------------+----------+

C20b v_cluster_redundancy
+------------+---------------+--------------+------------------------------+
| cluster_id | cluster_label | member_count | packages                     |
+------------+---------------+--------------+------------------------------+
|          3 | logging       |            2 | log4j-core, logback-classic  |
|          4 | http client   |            3 | axios, node-fetch, requests  |
|          5 | json parsing  |            2 | jackson-databind, json-smart |
|          6 | yaml parsing  |            2 | pyyaml, snakeyaml            |
+------------+---------------+--------------+------------------------------+

C20c v_package_abandonment (top 5)
+------------+-----------------+------------------+---------------+-----------+-------------------+
| run_date   | package_name    | maintainer_count | last_activity | days_idle | abandonment_score |
+------------+-----------------+------------------+---------------+-----------+-------------------+
| 2026-11-20 | body-parser     |                1 | 2024-03-01    |       994 |             100.0 |
| 2026-11-20 | express         |                1 | 2024-03-01    |       994 |             100.0 |
| 2026-11-20 | lodash          |                1 | 2023-06-10    |      1259 |             100.0 |
| 2026-11-20 | snakeyaml       |                1 | 2024-01-15    |      1040 |             100.0 |
| 2026-11-20 | logback-classic |                1 | 2025-08-02    |       475 |              82.5 |
+------------+-----------------+------------------+---------------+-----------+-------------------+
```

The C20c table above was captured before `v_package_abandonment` treated unknown activity (no
maintainer, or no `last_active_at`) as 730 days idle. Packages with no maintainer now score 100.0
too and join the tie at the top, so a re-run lists more than the four 100.0 rows shown; re-run
`sql/complex_queries.sql` to refresh it.

### Updatable view with CHECK OPTION (C22)

```sql
CREATE OR REPLACE VIEW v_prod_deployment AS
SELECT service_id, deploy_seq, environment, deployed_at, is_active
FROM   deployment
WHERE  environment = 'PROD'
WITH CHECK OPTION;
```

The view uses one table and no `GROUP BY`, `DISTINCT` or aggregates, so it is updatable: an insert
into the view goes into `deployment`. `WITH CHECK OPTION` rejects any row the view could not show.
A failed statement would stop the script, so the rejected insert runs inside a small stored
procedure that catches the error and prints it:

```
+---------------------------------------+------------------------------------------------------------------+
| statement                             | outcome                                                          |
+---------------------------------------+------------------------------------------------------------------+
| INSERT PROD row via v_prod_deployment | accepted: deployment (12, 2) now exists in the base table: 1 row |
+---------------------------------------+------------------------------------------------------------------+
+--------------------------------------+-------------------------------------------------------------------------+
| statement                            | outcome                                                                 |
+--------------------------------------+-------------------------------------------------------------------------+
| INSERT DEV row via v_prod_deployment | rejected: ERROR 1369: CHECK OPTION failed 'dephgraph.v_prod_deployment' |
+--------------------------------------+-------------------------------------------------------------------------+
+----------------+----------------------------+
| moment         | deployments_of_reports_svc |
+----------------+----------------------------+
| after ROLLBACK |                          1 |
+----------------+----------------------------+
```

### Recursive blast radius (C23, C24)

C23 walks `depends_on` **upwards** from every version with a HIGH or CRITICAL vulnerability, then
keeps the versions a live deployment uses **directly**. It finds both paths of the express diamond:

```
+---------------------+--------------+-------------+------+--------------------------------------------------------------------------------------------+
| osv_id              | service_name | environment | hops | path                                                                                       |
+---------------------+--------------+-------------+------+--------------------------------------------------------------------------------------------+
| GHSA-jfh8-c2jp-5v3q | auth-svc     | DEV         |    0 | log4j-core@2.14.1                                                                          |
| GHSA-jfh8-c2jp-5v3q | payments-svc | PROD        |    1 | spring-boot-starter-log4j2@2.5.0 -> log4j-core@2.14.1                                      |
| GHSA-8q59-q68h-6hv4 | reports-svc  | PROD        |    0 | pyyaml@5.3.1                                                                               |
| GHSA-7rjr-3q55-vv33 | auth-svc     | DEV         |    0 | log4j-core@2.14.1                                                                          |
| GHSA-7rjr-3q55-vv33 | payments-svc | PROD        |    1 | spring-boot-starter-log4j2@2.5.0 -> log4j-core@2.14.1                                      |
| GHSA-mjmj-j48q-9wg2 | auth-svc     | PROD        |    2 | spring-boot-starter-web@2.6.6 -> spring-boot-starter@2.6.6 -> snakeyaml@1.29               |
| GHSA-cph5-m8f7-6c5x | web-frontend | PROD        |    0 | axios@0.21.1                                                                               |
| GHSA-hrpp-h998-j3pp | web-frontend | PROD        |    1 | express@4.17.1 -> qs@6.7.0                                                                 |
| GHSA-hrpp-h998-j3pp | web-frontend | PROD        |    2 | express@4.17.1 -> body-parser@1.19.0 -> qs@6.7.0                                           |
| GHSA-jjjh-jjxp-wpff | auth-svc     | PROD        |    2 | spring-boot-starter-web@2.6.6 -> spring-boot-starter-json@2.6.6 -> jackson-databind@2.13.2 |
| GHSA-35jh-r3h4-6jhm | web-frontend | PROD        |    0 | lodash@4.17.20                                                                             |
+---------------------+--------------+-------------+------+--------------------------------------------------------------------------------------------+
```

C24 walks **downwards** from auth-svc's direct dependencies and compares the depth it computes with
the depth stored in `resolves_to`. They agree, which shows the stored closure is consistent with the
graph:

```
+--------------------------+----------------+----------------+--------------+--------------+
| package_name             | version_string | computed_depth | stored_depth | check_result |
+--------------------------+----------------+----------------+--------------+--------------+
| log4j-core               | 2.17.1         |              0 |            0 | ok           |
| spring-boot-starter-web  | 2.6.6          |              0 |            0 | ok           |
| spring-boot-starter      | 2.6.6          |              1 |            1 | ok           |
| spring-boot-starter-json | 2.6.6          |              1 |            1 | ok           |
| jackson-databind         | 2.13.2         |              2 |            2 | ok           |
| snakeyaml                | 1.29           |              2 |            2 | ok           |
| jackson-core             | 2.13.2         |              3 |            3 | ok           |
+--------------------------+----------------+----------------+--------------+--------------+
```

Full output of every query is in `logs/complex_queries.log` after a run.

---

## What comes next

Phase 5 (Unit IV): transactions, schedules, conflict serializability and two-phase locking, using a
separate database so this one stays as the clean sample.
