-- =====================================================================
-- DephGraph — constraint violation demos (MySQL 8.4)
--
-- Runs the 12 insert / delete / update cases from
-- relational-algebra/README.md, Part 2, on the Phase 2 sample data,
-- and shows what MySQL really does: the error it raises, or the rows
-- that change.
--
-- Every case runs inside START TRANSACTION ... ROLLBACK, so the sample
-- data is the same before and after each case (and after the script).
-- X1-X2 are extra: the two ">= 0" domain rules added as CHECKs in Phase 4.
--
-- Run (errors are expected, so use --force):
--   scripts/mysql.sh run er-diagram/schema.sql
--   scripts/mysql.sh run sql/seed.sql
--   scripts/mysql.sh run --force sql/constraint_demos.sql
-- Output: logs/constraint_demos.log
-- =====================================================================

USE dephgraph;

-- =====================================================================
-- 2.1 INSERT
-- =====================================================================

SELECT 'I1: INSERT a service for team 9, which does not exist' AS demo,
       'rejected: referential integrity (ERROR 1452)' AS expected;
START TRANSACTION;
INSERT INTO service (service_id, service_name, criticality, team_id)
VALUES (12, 'billing-svc', 'HIGH', 9);
ROLLBACK;

SELECT 'I2: INSERT a deployment with service_id NULL' AS demo,
       'rejected: entity integrity, part of the PK is NULL (ERROR 1048)' AS expected;
START TRANSACTION;
INSERT INTO deployment (service_id, deploy_seq, environment, deployed_at)
VALUES (NULL, 3, 'PROD', '2026-11-01 10:00:00');
ROLLBACK;

SELECT 'I3: INSERT a second team called Payments' AS demo,
       'rejected: key constraint, team_name is UNIQUE (ERROR 1062)' AS expected;
START TRANSACTION;
INSERT INTO team (team_id, team_name, contact_email)
VALUES (3, 'Payments', 'payments2@example.com');
ROLLBACK;

SELECT 'I4: INSERT a vulnerability with cvss_score 11.5' AS demo,
       'rejected: domain constraint chk_cvss, 0 <= cvss <= 10 (ERROR 3819)' AS expected;
START TRANSACTION;
INSERT INTO vulnerability (vuln_id, osv_id, severity, cvss_score, published_at)
VALUES (902, 'GHSA-xxxx', 'HIGH', 11.5, '2026-11-01');
ROLLBACK;

SELECT 'I5: INSERT log4j-core 2.14.1 depending on itself' AS demo,
       'rejected: semantic constraint chk_no_self_dep (ERROR 3819)' AS expected;
START TRANSACTION;
INSERT INTO depends_on (dependent_package_id, dependent_version,
                        dependency_package_id, dependency_version, scope, is_direct)
VALUES (100, '2.14.1', 100, '2.14.1', 'runtime', TRUE);
ROLLBACK;

SELECT 'I6: INSERT package gson with cluster_id NULL' AS demo,
       'accepted: GROUPED_INTO has partial participation, so NULL is allowed' AS expected;
START TRANSACTION;
INSERT INTO package (package_id, package_name, ecosystem, cluster_id)
VALUES (103, 'gson', 'Maven', NULL);
SELECT package_id, package_name, ecosystem, cluster_id FROM package WHERE package_id = 103;
ROLLBACK;

-- =====================================================================
-- 2.2 DELETE
-- =====================================================================

SELECT 'D1: DELETE team 1' AS demo,
       'rejected: ON DELETE RESTRICT, service 10 and plan 50 still reference it (ERROR 1451)' AS expected;
START TRANSACTION;
DELETE FROM team WHERE team_id = 1;
ROLLBACK;

SELECT 'D2: DELETE service 10' AS demo,
       'cascades: 2 deployments, 3 resolves_to, 2 observes, 1 pr_review, 2 pr_risk_verdict, 1 waived_for = 11 rows' AS expected;
START TRANSACTION;
-- rows that belong to service 10 before the delete
SELECT 'before' AS moment,
       (SELECT COUNT(*) FROM deployment      WHERE service_id = 10) AS deployment,
       (SELECT COUNT(*) FROM resolves_to     WHERE service_id = 10) AS resolves_to,
       (SELECT COUNT(*) FROM observes        WHERE service_id = 10) AS observes,
       (SELECT COUNT(*) FROM pr_review       WHERE service_id = 10) AS pr_review,
       (SELECT COUNT(*) FROM pr_risk_verdict WHERE service_id = 10) AS pr_risk_verdict,
       (SELECT COUNT(*) FROM waived_for      WHERE service_id = 10) AS waived_for;
DELETE FROM service WHERE service_id = 10;
SELECT 'after' AS moment,
       (SELECT COUNT(*) FROM deployment      WHERE service_id = 10) AS deployment,
       (SELECT COUNT(*) FROM resolves_to     WHERE service_id = 10) AS resolves_to,
       (SELECT COUNT(*) FROM observes        WHERE service_id = 10) AS observes,
       (SELECT COUNT(*) FROM pr_review       WHERE service_id = 10) AS pr_review,
       (SELECT COUNT(*) FROM pr_risk_verdict WHERE service_id = 10) AS pr_risk_verdict,
       (SELECT COUNT(*) FROM waived_for      WHERE service_id = 10) AS waived_for;
ROLLBACK;

SELECT 'D3: DELETE licence 2 (LGPL-2.1-only)' AS demo,
       'set NULL: logback-classic 1.2.3 stays, with license_id = NULL' AS expected;
START TRANSACTION;
DELETE FROM license WHERE license_id = 2;
SELECT package_id, version_string, license_id
FROM package_version WHERE package_id = 101 AND version_string = '1.2.3';
ROLLBACK;

-- =====================================================================
-- 2.3 UPDATE
-- =====================================================================

SELECT 'U1: UPDATE service 11 to belong to team 9' AS demo,
       'rejected: referential integrity, there is no team 9 (ERROR 1452)' AS expected;
START TRANSACTION;
UPDATE service SET team_id = 9 WHERE service_id = 11;
ROLLBACK;

SELECT 'U2: UPDATE the key of log4j-core 2.14.1 to 2.14.2' AS demo,
       'rejected: the key is referenced and there is no ON UPDATE CASCADE (ERROR 1451)' AS expected;
START TRANSACTION;
UPDATE package_version SET version_string = '2.14.2'
WHERE package_id = 100 AND version_string = '2.14.1';
ROLLBACK;

SELECT 'U3: UPDATE cvss_score of vulnerability 901 to 9.8' AS demo,
       'accepted: non-key attribute, value inside its domain' AS expected;
START TRANSACTION;
UPDATE vulnerability SET cvss_score = 9.8 WHERE vuln_id = 901;
SELECT vuln_id, osv_id, cvss_score FROM vulnerability WHERE vuln_id = 901;
ROLLBACK;

-- =====================================================================
-- Extra: the two ">= 0" domain rules (CHECKs added in Phase 4)
-- =====================================================================

SELECT 'X1: INSERT an observation with exec_count -1' AS demo,
       'rejected: domain constraint chk_exec_count (ERROR 3819)' AS expected;
START TRANSACTION;
INSERT INTO observes (service_id, deploy_seq, package_id, version_string, scan_id, exec_count)
VALUES (10, 2, 100, '2.14.1', 500, -1);
ROLLBACK;

SELECT 'X2: UPDATE a resolved dependency to depth -1' AS demo,
       'rejected: domain constraint chk_depth (ERROR 3819)' AS expected;
START TRANSACTION;
UPDATE resolves_to SET depth = -1
WHERE service_id = 10 AND deploy_seq = 1 AND package_id = 100 AND version_string = '2.14.1';
ROLLBACK;

-- =====================================================================
-- Check: the sample data is unchanged (same counts as after seed.sql)
-- =====================================================================
SELECT (SELECT COUNT(*) FROM team)            AS teams,
       (SELECT COUNT(*) FROM service)         AS services,
       (SELECT COUNT(*) FROM deployment)      AS deployments,
       (SELECT COUNT(*) FROM package)         AS packages,
       (SELECT COUNT(*) FROM package_version) AS versions,
       (SELECT COUNT(*) FROM vulnerability)   AS vulns,
       (SELECT cvss_score FROM vulnerability WHERE vuln_id = 901) AS cvss_901;
