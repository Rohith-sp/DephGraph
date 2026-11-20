-- =====================================================================
-- DephGraph — Q1-Q7 in SQL (MySQL 8.4)
--
-- The seven relational-algebra queries of relational-algebra/README.md,
-- Part 3, written in SQL. Each query starts with a label row that gives
-- the question and the result the RA document computed by hand, so the
-- log shows the expected result right above the real one.
--
-- Run on the BASE sample data only (seed.sql, no seed_extra.sql),
-- because the hand-computed results are for those rows:
--   scripts/mysql.sh run er-diagram/schema.sql
--   scripts/mysql.sh run sql/seed.sql
--   scripts/mysql.sh run sql/queries.sql
-- Output: logs/queries.log
--
-- The script changes nothing: the one INSERT (Q6) is rolled back.
-- =====================================================================

USE dephgraph;

-- =====================================================================
-- Q1 — Blast radius: which services run a version affected by Log4Shell?
-- =====================================================================

-- Q1a: direct translation of the RA expression.
--   V  = the vulnerability, AV = affected versions, PROD = live PROD deployments,
--   RESOLVES_TO already holds every version a deployment reaches (any depth).
SELECT 'Q1a: services whose live PROD deployment resolves to a version affected by GHSA-jfh8-c2jp-5v3q' AS query,
       '{payments-svc}' AS expected;
SELECT DISTINCT s.service_name
FROM   vulnerability v
JOIN   affects       a  ON a.vuln_id = v.vuln_id
JOIN   resolves_to   rt ON rt.package_id = a.package_id
                       AND rt.version_string = a.version_string
JOIN   deployment    d  ON d.service_id = rt.service_id
                       AND d.deploy_seq = rt.deploy_seq
JOIN   service       s  ON s.service_id = d.service_id
WHERE  v.osv_id = 'GHSA-jfh8-c2jp-5v3q'
  AND  d.environment = 'PROD' AND d.is_active = TRUE;

-- Q1b: the same question WITHOUT trusting the stored closure in RESOLVES_TO.
--
-- Basic relational algebra cannot compute a transitive closure: every
-- extra level of DEPENDS_ON needs one more join, and a chain can be any
-- length (RA doc: LEVEL0, LEVEL1, LEVEL2, ...). SQL:1999 added recursive
-- common table expressions. WITH RECURSIVE repeats the recursive part
-- (one join with depends_on) until it adds no new rows, so it computes
-- LEVEL0 ∪ LEVEL1 ∪ LEVEL2 ∪ ... for a chain of any depth.
--
-- Step 1: walk UP the graph from the affected version ("who depends on me").
SELECT 'Q1b-1: every package version that reaches log4j-core 2.14.1 through depends_on (recursive CTE)' AS query,
       'LEVEL0 = {(100, 2.14.1)}, LEVEL1 = {(102, 2.5.0)}, LEVEL2 = empty' AS expected;
WITH RECURSIVE reaches (package_id, version_string, level, path) AS (
    -- anchor = LEVEL0: the affected version itself
    SELECT a.package_id, a.version_string, 0,
           CAST(CONCAT(p.package_name, '@', a.version_string) AS CHAR(1000))
    FROM   affects a
    JOIN   vulnerability v ON v.vuln_id = a.vuln_id
    JOIN   package p       ON p.package_id = a.package_id
    WHERE  v.osv_id = 'GHSA-jfh8-c2jp-5v3q'
    UNION ALL
    -- recursive step = LEVEL(n+1): versions that depend on a version in LEVEL(n)
    SELECT d.dependent_package_id, d.dependent_version, r.level + 1,
           CONCAT(p.package_name, '@', d.dependent_version, ' -> ', r.path)
    FROM   reaches r
    JOIN   depends_on d ON d.dependency_package_id = r.package_id
                       AND d.dependency_version    = r.version_string
    JOIN   package p    ON p.package_id = d.dependent_package_id
    WHERE  r.level < 20                                   -- guard against a cycle
)
SELECT level, package_id, version_string, path
FROM   reaches
ORDER  BY level, package_id;

-- Step 2: a service is hit if one of its DIRECT dependencies (depth = 0)
-- is in that set. Only direct rows of RESOLVES_TO are used here; the
-- recursion found the indirect path itself.
SELECT 'Q1b-2: services hit, using only direct dependencies (depth 0) plus the recursive CTE' AS query,
       '{payments-svc}, via spring-boot-starter-log4j2@2.5.0 -> log4j-core@2.14.1' AS expected;
WITH RECURSIVE reaches (package_id, version_string, level, path) AS (
    SELECT a.package_id, a.version_string, 0,
           CAST(CONCAT(p.package_name, '@', a.version_string) AS CHAR(1000))
    FROM   affects a
    JOIN   vulnerability v ON v.vuln_id = a.vuln_id
    JOIN   package p       ON p.package_id = a.package_id
    WHERE  v.osv_id = 'GHSA-jfh8-c2jp-5v3q'
    UNION ALL
    SELECT d.dependent_package_id, d.dependent_version, r.level + 1,
           CONCAT(p.package_name, '@', d.dependent_version, ' -> ', r.path)
    FROM   reaches r
    JOIN   depends_on d ON d.dependency_package_id = r.package_id
                       AND d.dependency_version    = r.version_string
    JOIN   package p    ON p.package_id = d.dependent_package_id
    WHERE  r.level < 20
)
SELECT s.service_name, d.environment, r.level AS hops_below_direct_dep, r.path
FROM   reaches r
JOIN   resolves_to rt ON rt.package_id = r.package_id
                     AND rt.version_string = r.version_string
                     AND rt.depth = 0
JOIN   deployment d   ON d.service_id = rt.service_id AND d.deploy_seq = rt.deploy_seq
JOIN   service s      ON s.service_id = d.service_id
WHERE  d.environment = 'PROD' AND d.is_active = TRUE;

-- =====================================================================
-- Q2 — Prune: which package versions are deployed but never executed?
-- =====================================================================
-- RES − LIVE, then join PACKAGE for the name.

SELECT 'Q2a: deployed but never executed, with EXCEPT (set difference)' AS query,
       '{(10, spring-boot-starter-log4j2, 2.5.0), (10, logback-classic, 1.2.3)}' AS expected;
SELECT dead.service_id, p.package_name, dead.version_string
FROM (
        SELECT service_id, deploy_seq, package_id, version_string FROM resolves_to      -- RES
        EXCEPT
        SELECT service_id, deploy_seq, package_id, version_string FROM observes
        WHERE  exec_count > 0                                                          -- LIVE
     ) AS dead
JOIN package p ON p.package_id = dead.package_id
ORDER BY dead.service_id, p.package_name DESC;

SELECT 'Q2b: the same with NOT EXISTS (works on any SQL database, also before EXCEPT existed)' AS query,
       '{(10, spring-boot-starter-log4j2, 2.5.0), (10, logback-classic, 1.2.3)}' AS expected;
SELECT rt.service_id, p.package_name, rt.version_string
FROM   resolves_to rt
JOIN   package p ON p.package_id = rt.package_id
WHERE  NOT EXISTS (
         SELECT 1 FROM observes o
         WHERE  o.service_id = rt.service_id AND o.deploy_seq = rt.deploy_seq
           AND  o.package_id = rt.package_id AND o.version_string = rt.version_string
           AND  o.exec_count > 0)
ORDER BY rt.service_id, p.package_name DESC;

-- =====================================================================
-- Q3 — Minimum remediation: which upgrade fixes the most?
-- =====================================================================
-- E = PROD ⋈ RESOLVES_TO ⋈ AFFECTS; the ℱ (aggregate) operator becomes
-- GROUP BY with COUNT(DISTINCT ...). One round of greedy set cover.

SELECT 'Q3: per affected version in live PROD, how many services and vulnerabilities an upgrade fixes' AS query,
       '{(100, 2.14.1, services = 1, vulns = 2)}' AS expected;
SELECT rt.package_id, rt.version_string,
       COUNT(DISTINCT rt.service_id) AS services,
       COUNT(DISTINCT a.vuln_id)     AS vulns
FROM   deployment  d
JOIN   resolves_to rt ON rt.service_id = d.service_id AND rt.deploy_seq = d.deploy_seq
JOIN   affects     a  ON a.package_id = rt.package_id AND a.version_string = rt.version_string
WHERE  d.environment = 'PROD' AND d.is_active = TRUE
GROUP  BY rt.package_id, rt.version_string
ORDER  BY services DESC, vulns DESC;

-- =====================================================================
-- Q4 — Licence propagation: which services ship copyleft code?
-- =====================================================================

SELECT 'Q4: services that resolve to a package version under a copyleft licence' AS query,
       '{(payments-svc, logback-classic, 1.2.3, LGPL-2.1-only)}' AS expected;
SELECT DISTINCT s.service_name, p.package_name, pv.version_string, l.spdx_id
FROM   license         l
JOIN   package_version pv ON pv.license_id = l.license_id
JOIN   resolves_to     rt ON rt.package_id = pv.package_id
                         AND rt.version_string = pv.version_string
JOIN   service         s  ON s.service_id = rt.service_id
JOIN   package         p  ON p.package_id = pv.package_id
WHERE  l.is_copyleft = TRUE;

-- =====================================================================
-- Q5 — Consolidate: which services use two packages that do the same job?
-- =====================================================================
-- Self-join: SP and its renamed copy SP2 (here the aliases sp1 / sp2).

SELECT 'Q5: services that use two different packages from the same semantic cluster (self-join)' AS query,
       '{(payments-svc, logging)}' AS expected;
SELECT DISTINCT s.service_name, sc.cluster_label
FROM  (SELECT DISTINCT rt.service_id, p.package_id, p.cluster_id
       FROM resolves_to rt JOIN package p ON p.package_id = rt.package_id
       WHERE p.cluster_id IS NOT NULL) AS sp1
JOIN  (SELECT DISTINCT rt.service_id, p.package_id, p.cluster_id
       FROM resolves_to rt JOIN package p ON p.package_id = rt.package_id
       WHERE p.cluster_id IS NOT NULL) AS sp2
      ON  sp2.service_id = sp1.service_id
      AND sp2.cluster_id = sp1.cluster_id
      AND sp1.package_id < sp2.package_id        -- different packages, each pair once
JOIN  service          s  ON s.service_id = sp1.service_id
JOIN  semantic_cluster sc ON sc.cluster_id = sp1.cluster_id;

-- =====================================================================
-- Q6 — Division: which maintainers maintain EVERY package in the logging cluster?
-- =====================================================================
-- SQL has no ÷ operator. Two standard ways to write it:

SELECT 'Q6a: division with double NOT EXISTS ("no logging package that this maintainer does not maintain")' AS query,
       'empty set' AS expected;
-- An empty result prints nothing in the log, so the query is wrapped in a
-- count: rows_returned = 0 shows the empty set.
SELECT COUNT(*) AS rows_returned, IFNULL(GROUP_CONCAT(q.handle), '-') AS handles FROM (
SELECT m.handle
FROM   maintainer m
WHERE  EXISTS (SELECT 1 FROM maintained_by mb WHERE mb.maintainer_id = m.maintainer_id)  -- π A (R)
  AND  NOT EXISTS (
         SELECT 1
         FROM   package p
         JOIN   semantic_cluster sc ON sc.cluster_id = p.cluster_id
         WHERE  sc.cluster_label = 'logging'
           AND  NOT EXISTS (
                  SELECT 1 FROM maintained_by mb
                  WHERE  mb.maintainer_id = m.maintainer_id
                    AND  mb.package_id    = p.package_id))
) AS q;

SELECT 'Q6b: division with GROUP BY ... HAVING COUNT = size of the logging cluster' AS query,
       'empty set' AS expected;
SELECT COUNT(*) AS rows_returned, IFNULL(GROUP_CONCAT(q.handle), '-') AS handles FROM (
SELECT m.handle
FROM   maintained_by mb
JOIN   package p          ON p.package_id = mb.package_id
JOIN   semantic_cluster sc ON sc.cluster_id = p.cluster_id
JOIN   maintainer m       ON m.maintainer_id = mb.maintainer_id
WHERE  sc.cluster_label = 'logging'
GROUP  BY m.maintainer_id, m.handle
HAVING COUNT(DISTINCT mb.package_id) = (
         SELECT COUNT(*) FROM package p2
         JOIN   semantic_cluster sc2 ON sc2.cluster_id = p2.cluster_id
         WHERE  sc2.cluster_label = 'logging')
) AS q;

-- The RA document's "what if": add MAINTAINED_BY (101, 200), i.e. mdev also
-- maintains logback-classic. Done inside a transaction and rolled back.
SELECT 'Q6c: same two queries after adding maintained_by (package 101, maintainer 200), then ROLLBACK' AS query,
       '{mdev} from both' AS expected;
START TRANSACTION;
INSERT INTO maintained_by (package_id, maintainer_id, role, since_date)
VALUES (101, 200, 'COMMITTER', '2026-11-02');

SELECT 'double NOT EXISTS' AS method, m.handle
FROM   maintainer m
WHERE  EXISTS (SELECT 1 FROM maintained_by mb WHERE mb.maintainer_id = m.maintainer_id)
  AND  NOT EXISTS (
         SELECT 1
         FROM   package p
         JOIN   semantic_cluster sc ON sc.cluster_id = p.cluster_id
         WHERE  sc.cluster_label = 'logging'
           AND  NOT EXISTS (
                  SELECT 1 FROM maintained_by mb
                  WHERE  mb.maintainer_id = m.maintainer_id
                    AND  mb.package_id    = p.package_id));

SELECT 'GROUP BY ... HAVING' AS method, m.handle
FROM   maintained_by mb
JOIN   package p          ON p.package_id = mb.package_id
JOIN   semantic_cluster sc ON sc.cluster_id = p.cluster_id
JOIN   maintainer m       ON m.maintainer_id = mb.maintainer_id
WHERE  sc.cluster_label = 'logging'
GROUP  BY m.maintainer_id, m.handle
HAVING COUNT(DISTINCT mb.package_id) = (
         SELECT COUNT(*) FROM package p2
         JOIN   semantic_cluster sc2 ON sc2.cluster_id = p2.cluster_id
         WHERE  sc2.cluster_label = 'logging');
ROLLBACK;

SELECT 'Q6d: check after ROLLBACK, maintained_by is back to the sample rows' AS query,
       '2 rows' AS expected;
SELECT COUNT(*) AS maintained_by_rows FROM maintained_by;

-- =====================================================================
-- Q7 — Union, intersection and difference: waived and open exposure
-- =====================================================================
-- AFF and WAIVED are union-compatible: (vuln_id, service_id) in both.
-- The waiver date is fixed at '2026-11-02' as in the RA document, not
-- CURDATE(), so the result does not change when the waiver expires.

SELECT 'Q7a: AFF INTERSECT WAIVED, exposures the team has accepted for now' AS query,
       '{(901, 10)}' AS expected;
SELECT a.vuln_id, rt.service_id                                  -- AFF
FROM   resolves_to rt
JOIN   affects a ON a.package_id = rt.package_id AND a.version_string = rt.version_string
INTERSECT
SELECT vuln_id, service_id FROM waived_for                       -- WAIVED
WHERE  expires_on >= '2026-11-02';

SELECT 'Q7b: AFF EXCEPT WAIVED, exposures that still need fixing' AS query,
       '{(900, 10)}' AS expected;
SELECT a.vuln_id, rt.service_id
FROM   resolves_to rt
JOIN   affects a ON a.package_id = rt.package_id AND a.version_string = rt.version_string
EXCEPT
SELECT vuln_id, service_id FROM waived_for
WHERE  expires_on >= '2026-11-02';

SELECT 'Q7c: packages to review because they are copyleft UNION vulnerable' AS query,
       '{100, 101}' AS expected;
SELECT pv.package_id
FROM   package_version pv
JOIN   license l ON l.license_id = pv.license_id
WHERE  l.is_copyleft = TRUE
UNION
SELECT package_id FROM affects
ORDER  BY package_id;
