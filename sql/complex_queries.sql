-- =====================================================================
-- DephGraph — "more complex SQL" (syllabus Unit III), MySQL 8.4
--
-- Each query starts with a label row: the question in plain English and
-- the SQL feature it shows. Runs on the base sample data PLUS the extra
-- rows:
--   scripts/mysql.sh run er-diagram/schema.sql
--   scripts/mysql.sh run sql/seed.sql
--   scripts/mysql.sh run sql/seed_extra.sql
--   scripts/mysql.sh run sql/complex_queries.sql
-- Output: logs/complex_queries.log
--
-- Dates: "today" is fixed with @as_of so the results do not change from
-- day to day. Only C19 (the existing views) uses CURDATE(), because the
-- views in schema.sql are written that way.
--
-- The script leaves the data unchanged. It creates two views (C20, C21);
-- all inserts are inside transactions that are rolled back.
-- =====================================================================

USE dephgraph;

SET @as_of = DATE '2026-11-21';

-- =====================================================================
-- A. Nested subqueries
-- =====================================================================

SELECT 'C1: Which vulnerabilities score above the average CVSS?' AS question,
       'nested (uncorrelated) subquery in WHERE; AVG ignores the NULL score' AS shows;
SELECT osv_id, severity, cvss_score,
       (SELECT ROUND(AVG(cvss_score), 2) FROM vulnerability) AS avg_cvss
FROM   vulnerability
WHERE  cvss_score > (SELECT AVG(cvss_score) FROM vulnerability)
ORDER  BY cvss_score DESC, osv_id;

SELECT 'C2: Which services run, in a live PROD deployment, a version with a CRITICAL vulnerability?' AS question,
       'IN with subqueries nested three levels deep' AS shows;
SELECT service_name
FROM   service
WHERE  service_id IN (
         SELECT rt.service_id
         FROM   resolves_to rt
         JOIN   deployment d ON d.service_id = rt.service_id AND d.deploy_seq = rt.deploy_seq
         WHERE  d.environment = 'PROD' AND d.is_active = TRUE
           AND  (rt.package_id, rt.version_string) IN (
                  SELECT a.package_id, a.version_string
                  FROM   affects a
                  WHERE  a.vuln_id IN (SELECT vuln_id FROM vulnerability
                                       WHERE  severity = 'CRITICAL')))
ORDER  BY service_name;

SELECT 'C3: Which packages are in the catalogue but used by no deployment?' AS question,
       'NOT IN with a subquery' AS shows;
SELECT package_id, package_name, ecosystem
FROM   package
WHERE  package_id NOT IN (SELECT package_id FROM resolves_to)
ORDER  BY package_id;

-- =====================================================================
-- B. NULLs and three-valued logic
-- =====================================================================

SELECT 'C4: How does SQL compare with NULL?' AS question,
       'three-valued logic: TRUE / FALSE / UNKNOWN (shown as 1 / 0 / NULL)' AS shows;
SELECT NULL = NULL          AS `NULL = NULL`,
       NULL <> 5            AS `NULL <> 5`,
       NULL IS NULL         AS `NULL IS NULL`,
       5 IN (5, NULL)       AS `5 IN (5,NULL)`,
       7 IN (5, NULL)       AS `7 IN (5,NULL)`,
       7 NOT IN (5, NULL)   AS `7 NOT IN (5,NULL)`,
       (NULL AND FALSE)     AS `NULL AND FALSE`,
       (NULL OR TRUE)       AS `NULL OR TRUE`;

SELECT 'C5: Split the vulnerabilities by "cvss_score >= 7". Do the two halves add up to all of them?' AS question,
       'WHERE keeps only TRUE rows, so a NULL score falls in neither half' AS shows;
SELECT (SELECT COUNT(*) FROM vulnerability)                          AS total,
       (SELECT COUNT(*) FROM vulnerability WHERE cvss_score >= 7)       AS ge_7,
       (SELECT COUNT(*) FROM vulnerability WHERE NOT (cvss_score >= 7)) AS not_ge_7,
       (SELECT COUNT(*) FROM vulnerability WHERE cvss_score IS NULL)    AS score_is_null,
       (SELECT GROUP_CONCAT(osv_id) FROM vulnerability WHERE cvss_score IS NULL) AS which_one;

SELECT 'C6a: Which packages are NOT the representative of any cluster? (NOT IN, wrong)' AS question,
       'NOT IN pitfall: cluster "http client" has representative NULL, so every test is UNKNOWN and 0 rows come back' AS shows;
SELECT COUNT(*) AS rows_returned
FROM   package
WHERE  package_id NOT IN (SELECT representative_package_id FROM semantic_cluster);

SELECT 'C6b: the same question, two correct ways' AS question,
       'filter the NULL out of the subquery, or use NOT EXISTS (never UNKNOWN)' AS shows;
SELECT 'NOT IN ... IS NOT NULL' AS method, COUNT(*) AS rows_returned
FROM   package
WHERE  package_id NOT IN (SELECT representative_package_id FROM semantic_cluster
                          WHERE  representative_package_id IS NOT NULL)
UNION ALL
SELECT 'NOT EXISTS', COUNT(*)
FROM   package p
WHERE  NOT EXISTS (SELECT 1 FROM semantic_cluster sc
                   WHERE  sc.representative_package_id = p.package_id);

-- =====================================================================
-- C. Correlated subqueries, EXISTS / NOT EXISTS
-- =====================================================================

SELECT 'C7: Which deployed package versions are older than the newest version we know of that package?' AS question,
       'correlated subquery: the inner query uses pv.package_id from the outer row' AS shows;
SELECT DISTINCT p.package_name, pv.version_string AS deployed, pv.published_at,
       (SELECT pv2.version_string FROM package_version pv2
        WHERE  pv2.package_id = pv.package_id
        ORDER  BY pv2.published_at DESC LIMIT 1) AS newest_known
FROM   resolves_to rt
JOIN   package_version pv ON pv.package_id = rt.package_id AND pv.version_string = rt.version_string
JOIN   package p          ON p.package_id = pv.package_id
WHERE  pv.published_at < (SELECT MAX(pv2.published_at) FROM package_version pv2
                          WHERE  pv2.package_id = pv.package_id)
ORDER  BY p.package_name;

SELECT 'C8: For each service, how many packages does its live PROD deployment resolve to?' AS question,
       'correlated scalar subquery in the SELECT list (0 when the service has no PROD deployment)' AS shows;
SELECT s.service_name,
       (SELECT COUNT(*) FROM resolves_to rt
        JOIN   deployment d ON d.service_id = rt.service_id AND d.deploy_seq = rt.deploy_seq
        WHERE  d.service_id = s.service_id
          AND  d.environment = 'PROD' AND d.is_active = TRUE) AS prod_packages
FROM   service s
ORDER  BY prod_packages DESC, s.service_name;

SELECT 'C9: Which teams own a service with an open (not waived) vulnerability in live PROD?' AS question,
       'EXISTS with a correlated subquery, NOT EXISTS inside it for the waiver' AS shows;
SELECT t.team_name
FROM   team t
WHERE  EXISTS (
         SELECT 1
         FROM   service s
         JOIN   deployment d  ON d.service_id = s.service_id
         JOIN   resolves_to rt ON rt.service_id = d.service_id AND rt.deploy_seq = d.deploy_seq
         JOIN   affects a     ON a.package_id = rt.package_id AND a.version_string = rt.version_string
         WHERE  s.team_id = t.team_id
           AND  d.environment = 'PROD' AND d.is_active = TRUE
           AND  NOT EXISTS (SELECT 1 FROM waived_for w
                            WHERE  w.vuln_id = a.vuln_id AND w.service_id = s.service_id
                              AND  w.expires_on >= @as_of))
ORDER  BY t.team_name;

SELECT 'C10: Which remediation plans have no actions yet, and which services were never deployed?' AS question,
       'NOT EXISTS (anti-join)' AS shows;
SELECT 'plan' AS kind, CONCAT('plan ', rp.plan_id, ' (', rp.status, ')') AS item
FROM   remediation_plan rp
WHERE  NOT EXISTS (SELECT 1 FROM remediation_action ra WHERE ra.plan_id = rp.plan_id)
UNION ALL
SELECT 'service', s.service_name
FROM   service s
WHERE  NOT EXISTS (SELECT 1 FROM deployment d WHERE d.service_id = s.service_id);

-- =====================================================================
-- D. ALL and ANY / SOME
-- =====================================================================

SELECT 'C11a: Which vulnerability has the highest CVSS? (>= ALL, wrong)' AS question,
       '>= ALL pitfall: one NULL score in the subquery makes every comparison UNKNOWN, so 0 rows' AS shows;
SELECT COUNT(*) AS rows_returned
FROM   vulnerability
WHERE  cvss_score >= ALL (SELECT cvss_score FROM vulnerability);

SELECT 'C11b: the same with the NULL filtered out' AS question,
       '>= ALL over non-NULL scores' AS shows;
SELECT osv_id, severity, cvss_score
FROM   vulnerability
WHERE  cvss_score >= ALL (SELECT cvss_score FROM vulnerability WHERE cvss_score IS NOT NULL);

SELECT 'C12: Which vulnerabilities are worse than at least one of the two Log4Shell-family advisories in the base data?' AS question,
       '> ANY (same as > SOME): true if greater than some value in the list (here > 9.0)' AS shows;
SELECT osv_id, severity, cvss_score
FROM   vulnerability
WHERE  cvss_score > ANY (SELECT v.cvss_score
                         FROM   vulnerability v
                         JOIN   affects a ON a.vuln_id = v.vuln_id
                         WHERE  a.package_id = 100)
ORDER  BY cvss_score DESC;

-- =====================================================================
-- E. Joins: inner, left, right, full outer
-- =====================================================================

SELECT 'C13: How many services does each team own, including teams with none?' AS question,
       'LEFT OUTER JOIN; COUNT(column) skips the NULL row, COUNT(*) does not' AS shows;
SELECT t.team_name,
       COUNT(s.service_id) AS services,
       COUNT(*)            AS rows_after_join,
       IFNULL(GROUP_CONCAT(s.service_name ORDER BY s.service_name), '-') AS service_names
FROM   team t
LEFT   JOIN service s ON s.team_id = t.team_id
GROUP  BY t.team_id, t.team_name
ORDER  BY t.team_id;

SELECT 'C14: Which dependencies judged by the PR gate are not in the package catalogue yet?' AS question,
       'LEFT JOIN ... WHERE right side IS NULL (pr_risk_verdict.dep_name has no FK on purpose)' AS shows;
SELECT v.service_id, v.pr_number, v.dep_name, v.dep_ecosystem, v.verdict
FROM   pr_risk_verdict v
LEFT   JOIN package p ON p.package_name = v.dep_name AND p.ecosystem = v.dep_ecosystem
WHERE  p.package_id IS NULL
ORDER  BY v.service_id, v.pr_number, v.dep_name;

SELECT 'C15: How many package versions use each licence, including unused licences?' AS question,
       'RIGHT OUTER JOIN (license is the preserved side)' AS shows;
SELECT l.spdx_id, l.is_copyleft, COUNT(pv.package_id) AS versions
FROM   package_version pv
RIGHT  JOIN license l ON l.license_id = pv.license_id
GROUP  BY l.license_id, l.spdx_id, l.is_copyleft
ORDER  BY versions DESC, l.spdx_id;

SELECT 'C16: Match every vulnerability with the remediation actions that resolve it, keeping unmatched rows on BOTH sides' AS question,
       'FULL OUTER JOIN, emulated as LEFT JOIN UNION RIGHT JOIN (MySQL has no FULL OUTER JOIN)' AS shows;
SELECT v.osv_id, ra.plan_id, ra.action_seq, ra.action_type
FROM   vulnerability v
LEFT   JOIN resolves r            ON r.vuln_id = v.vuln_id
LEFT   JOIN remediation_action ra ON ra.plan_id = r.plan_id AND ra.action_seq = r.action_seq
UNION
SELECT v.osv_id, ra.plan_id, ra.action_seq, ra.action_type
FROM   vulnerability v
JOIN   resolves r                  ON r.vuln_id = v.vuln_id
RIGHT  JOIN remediation_action ra  ON ra.plan_id = r.plan_id AND ra.action_seq = r.action_seq
ORDER  BY osv_id IS NULL, osv_id, plan_id, action_seq;

-- =====================================================================
-- F. Aggregates, GROUP BY / HAVING, CASE
-- =====================================================================

SELECT 'C17: Per severity: how many vulnerabilities, and their CVSS min / avg / max' AS question,
       'aggregate functions; COUNT(*) vs COUNT(cvss_score) differ when a score is NULL' AS shows;
SELECT severity,
       COUNT(*)                   AS vulns,
       COUNT(cvss_score)          AS with_score,
       MIN(cvss_score)            AS min_cvss,
       ROUND(AVG(cvss_score), 2)  AS avg_cvss,
       MAX(cvss_score)            AS max_cvss
FROM   vulnerability
GROUP  BY severity
ORDER  BY FIELD(severity, 'CRITICAL', 'HIGH', 'MEDIUM', 'LOW');

SELECT 'C18: Which live PROD services have 2 or more open (not waived) vulnerabilities, and how bad are they?' AS question,
       'GROUP BY + HAVING, and CASE to turn the worst score into a risk band' AS shows;
SELECT s.service_name,
       COUNT(DISTINCT a.vuln_id) AS open_vulns,
       MAX(v.cvss_score)         AS worst_cvss,
       CASE
         WHEN MAX(v.cvss_score) >= 9 THEN 'critical'
         WHEN MAX(v.cvss_score) >= 7 THEN 'high'
         WHEN MAX(v.cvss_score) >= 4 THEN 'medium'
         ELSE 'low'
       END                       AS risk_band
FROM   service s
JOIN   deployment d   ON d.service_id = s.service_id AND d.environment = 'PROD' AND d.is_active = TRUE
JOIN   resolves_to rt ON rt.service_id = d.service_id AND rt.deploy_seq = d.deploy_seq
JOIN   affects a      ON a.package_id = rt.package_id AND a.version_string = rt.version_string
JOIN   vulnerability v ON v.vuln_id = a.vuln_id
WHERE  NOT EXISTS (SELECT 1 FROM waived_for w
                   WHERE  w.vuln_id = a.vuln_id AND w.service_id = s.service_id
                     AND  w.expires_on >= @as_of)
GROUP  BY s.service_id, s.service_name
HAVING COUNT(DISTINCT a.vuln_id) >= 2
ORDER  BY worst_cvss DESC;

SELECT 'C19: How many resolved packages does each service have per environment? (a pivot table)' AS question,
       'SUM(CASE ...) to turn rows into columns; LEFT JOINs keep the never-deployed service' AS shows;
SELECT s.service_name,
       SUM(CASE WHEN d.environment = 'PROD'    THEN 1 ELSE 0 END) AS prod,
       SUM(CASE WHEN d.environment = 'STAGING' THEN 1 ELSE 0 END) AS staging,
       SUM(CASE WHEN d.environment = 'DEV'     THEN 1 ELSE 0 END) AS dev,
       COUNT(rt.package_id)                                       AS total
FROM   service s
LEFT   JOIN deployment d   ON d.service_id = s.service_id
LEFT   JOIN resolves_to rt ON rt.service_id = d.service_id AND rt.deploy_seq = d.deploy_seq
GROUP  BY s.service_id, s.service_name
ORDER  BY s.service_id;

-- =====================================================================
-- G. Views
-- =====================================================================

-- The three views from schema.sql. v_service_exposure and
-- v_package_abandonment use CURDATE(), so their numbers depend on the
-- day they run: the waiver on vuln 901 for payments-svc expires on
-- 2026-11-30, after which payments-svc shows 2 open vulns instead of 1;
-- days_idle and abandonment_score grow every day.
SELECT 'C20a: View v_service_exposure: open, unwaived vulnerabilities per live PROD service (as of CURDATE())' AS question,
       'querying a view with a join + LEFT JOIN anti-join + GROUP BY inside' AS shows;
SELECT CURDATE() AS run_date, e.* FROM v_service_exposure e ORDER BY e.open_vulns DESC, e.service_name;

SELECT 'C20b: View v_cluster_redundancy: clusters with more than one member package' AS question,
       'view with GROUP BY + HAVING + GROUP_CONCAT' AS shows;
SELECT * FROM v_cluster_redundancy ORDER BY cluster_id;

SELECT 'C20c: View v_package_abandonment: the 5 most abandoned packages (as of CURDATE())' AS question,
       'view with LEFT JOINs and CASE; ORDER BY + LIMIT on top of it' AS shows;
SELECT CURDATE() AS run_date, package_name, maintainer_count, last_activity, days_idle, abandonment_score
FROM   v_package_abandonment
ORDER  BY abandonment_score DESC, package_name
LIMIT  5;

-- A new view: every package version a live PROD deployment ships, with its
-- licence. Joins five tables once so later queries stay short.
CREATE OR REPLACE VIEW v_prod_inventory AS
SELECT s.service_name, p.package_name, p.ecosystem, rt.version_string, rt.depth,
       l.spdx_id, l.is_copyleft
FROM   service s
JOIN   deployment d       ON d.service_id = s.service_id
                         AND d.environment = 'PROD' AND d.is_active = TRUE
JOIN   resolves_to rt     ON rt.service_id = d.service_id AND rt.deploy_seq = d.deploy_seq
JOIN   package p          ON p.package_id = rt.package_id
JOIN   package_version pv ON pv.package_id = rt.package_id AND pv.version_string = rt.version_string
LEFT   JOIN license l     ON l.license_id = pv.license_id;

SELECT 'C21: New view v_prod_inventory: per service and ecosystem, how many packages ship to PROD and how many are copyleft?' AS question,
       'CREATE VIEW, then GROUP BY on the view' AS shows;
SELECT service_name, ecosystem,
       COUNT(*)                                        AS packages,
       SUM(CASE WHEN is_copyleft THEN 1 ELSE 0 END)    AS copyleft
FROM   v_prod_inventory
GROUP  BY service_name, ecosystem
ORDER  BY service_name, ecosystem;

-- An updatable view: one base table, no GROUP BY / DISTINCT / aggregates,
-- so INSERT through it goes to deployment. WITH CHECK OPTION rejects any
-- row the view itself could not show (here: anything that is not PROD).
CREATE OR REPLACE VIEW v_prod_deployment AS
SELECT service_id, deploy_seq, environment, deployed_at, is_active
FROM   deployment
WHERE  environment = 'PROD'
WITH CHECK OPTION;

-- A failing statement would stop this script, so the rejected INSERT runs
-- inside a small procedure that catches the error and prints its message.
-- (sql_notes off only so DROP ... IF EXISTS does not print "does not exist")
SET @old_sql_notes = @@sql_notes;
SET sql_notes = 0;
DROP PROCEDURE IF EXISTS try_insert_dev_via_view;
SET sql_notes = @old_sql_notes;
DELIMITER //
CREATE PROCEDURE try_insert_dev_via_view()
BEGIN
  DECLARE CONTINUE HANDLER FOR SQLEXCEPTION
  BEGIN
    GET DIAGNOSTICS CONDITION 1 @errno = MYSQL_ERRNO, @msg = MESSAGE_TEXT;
    SELECT 'INSERT DEV row via v_prod_deployment' AS statement,
           CONCAT('rejected: ERROR ', @errno, ': ', @msg) AS outcome;
  END;
  INSERT INTO v_prod_deployment (service_id, deploy_seq, environment, deployed_at, is_active)
  VALUES (12, 3, 'DEV', '2026-11-21 09:00:00', TRUE);
END //
DELIMITER ;

SELECT 'C22: Updatable view WITH CHECK OPTION: insert a PROD row (accepted) and a DEV row (rejected), then ROLLBACK' AS question,
       'updatable view, WITH CHECK OPTION, transaction rolled back' AS shows;
START TRANSACTION;
INSERT INTO v_prod_deployment (service_id, deploy_seq, environment, deployed_at, is_active)
VALUES (12, 2, 'PROD', '2026-11-21 09:00:00', TRUE);
SELECT 'INSERT PROD row via v_prod_deployment' AS statement,
       CONCAT('accepted: deployment (12, 2) now exists in the base table: ',
              (SELECT COUNT(*) FROM deployment WHERE service_id = 12 AND deploy_seq = 2), ' row') AS outcome;
CALL try_insert_dev_via_view();
ROLLBACK;
DROP PROCEDURE try_insert_dev_via_view;
SELECT 'after ROLLBACK' AS moment, COUNT(*) AS deployments_of_reports_svc
FROM   deployment WHERE service_id = 12;

-- =====================================================================
-- H. Recursive CTE: blast radius over multi-level chains
-- =====================================================================

SELECT 'C23: Blast radius of every HIGH or CRITICAL vulnerability: which live deployments reach it, through which path?' AS question,
       'WITH RECURSIVE walking depends_on upwards to any depth, joined to direct dependencies only' AS shows;
WITH RECURSIVE reaches (vuln_id, package_id, version_string, hops, path) AS (
    SELECT a.vuln_id, a.package_id, a.version_string, 0,
           CAST(CONCAT(p.package_name, '@', a.version_string) AS CHAR(1000))
    FROM   affects a
    JOIN   vulnerability v ON v.vuln_id = a.vuln_id
    JOIN   package p       ON p.package_id = a.package_id
    WHERE  v.severity IN ('HIGH', 'CRITICAL')
    UNION ALL
    SELECT r.vuln_id, d.dependent_package_id, d.dependent_version, r.hops + 1,
           CONCAT(p.package_name, '@', d.dependent_version, ' -> ', r.path)
    FROM   reaches r
    JOIN   depends_on d ON d.dependency_package_id = r.package_id
                       AND d.dependency_version    = r.version_string
    JOIN   package p    ON p.package_id = d.dependent_package_id
    WHERE  r.hops < 20
)
SELECT v.osv_id, s.service_name, dep.environment, r.hops, r.path
FROM   reaches r
JOIN   vulnerability v ON v.vuln_id = r.vuln_id
JOIN   resolves_to rt  ON rt.package_id = r.package_id
                      AND rt.version_string = r.version_string
                      AND rt.depth = 0                       -- direct dependencies only
JOIN   deployment dep  ON dep.service_id = rt.service_id AND dep.deploy_seq = rt.deploy_seq
JOIN   service s       ON s.service_id = dep.service_id
WHERE  dep.is_active = TRUE
ORDER  BY v.cvss_score DESC, v.osv_id, s.service_name, r.hops, r.path;

SELECT 'C24: Rebuild the full dependency tree of auth-svc PROD from its direct dependencies; does it match the stored depth in resolves_to?' AS question,
       'WITH RECURSIVE walking downwards; MIN(level) per version; checked against the stored closure' AS shows;
WITH RECURSIVE tree (package_id, version_string, level) AS (
    SELECT rt.package_id, rt.version_string, 0
    FROM   resolves_to rt
    WHERE  rt.service_id = 11 AND rt.deploy_seq = 1 AND rt.depth = 0
    UNION ALL
    SELECT d.dependency_package_id, d.dependency_version, t.level + 1
    FROM   tree t
    JOIN   depends_on d ON d.dependent_package_id = t.package_id
                       AND d.dependent_version    = t.version_string
    WHERE  t.level < 20
)
SELECT p.package_name, t.version_string,
       MIN(t.level) AS computed_depth,
       rt.depth     AS stored_depth,
       IF(MIN(t.level) = rt.depth, 'ok', 'MISMATCH') AS check_result
FROM   tree t
JOIN   package p ON p.package_id = t.package_id
LEFT   JOIN resolves_to rt ON rt.service_id = 11 AND rt.deploy_seq = 1
                          AND rt.package_id = t.package_id
                          AND rt.version_string = t.version_string
GROUP  BY t.package_id, p.package_name, t.version_string, rt.depth
ORDER  BY computed_depth, p.package_name;

-- =====================================================================
-- I. Ordering and LIMIT
-- =====================================================================

SELECT 'C25: Top 3 upgrades to do first: affected versions in live PROD, ranked by worst CVSS, then by services hit' AS question,
       'GROUP BY, ORDER BY several keys (DESC), LIMIT' AS shows;
SELECT p.package_name, rt.version_string,
       MAX(v.cvss_score)              AS worst_cvss,
       COUNT(DISTINCT rt.service_id)  AS services,
       COUNT(DISTINCT a.vuln_id)      AS vulns,
       MAX(a.fixed_in)                AS upgrade_to   -- highest fix clears all; text MAX is fine for these values
FROM   deployment d
JOIN   resolves_to rt  ON rt.service_id = d.service_id AND rt.deploy_seq = d.deploy_seq
JOIN   affects a       ON a.package_id = rt.package_id AND a.version_string = rt.version_string
JOIN   vulnerability v ON v.vuln_id = a.vuln_id
JOIN   package p       ON p.package_id = rt.package_id
WHERE  d.environment = 'PROD' AND d.is_active = TRUE
GROUP  BY p.package_name, rt.version_string
ORDER  BY worst_cvss DESC, services DESC, p.package_name
LIMIT  3;
