-- =====================================================================
-- DephGraph — Phase 5: atomicity of a manifest ingest (MySQL 8.4, InnoDB)
--
-- In DephGraph, ingesting one lockfile ("manifest") for a service writes
--   1. one new DEPLOYMENT row       (service_id, deploy_seq)
--   2. one RESOLVES_TO row per resolved package version
-- These rows only make sense together. A deployment with half of its
-- packages would make blast-radius and exposure queries silently wrong.
-- So one ingest = one transaction: all rows or none.
--
-- The real app does the same thing on Postgres
-- (apps/api/app/routers/ingest.py: `async with session.begin():` around
-- _upsert_packages, upsert_advisories, _record_deployment, _record_scan).
-- This file shows the idea on the course schema in MySQL.
--
-- Parts
--   (a) a row fails half-way -> ROLLBACK -> nothing was stored
--   (b) all rows are fine    -> COMMIT   -> rows are stored
--   (c) SAVEPOINT / ROLLBACK TO SAVEPOINT (undo only part of the work)
--   (d) autocommit: every statement is its own transaction
--
-- IMPORTANT: when a statement fails inside a transaction, MySQL undoes
-- only that one statement. The transaction stays open and the earlier
-- statements stay in it. The client (here: this script; in the app:
-- SQLAlchemy's `session.begin()` block) must issue ROLLBACK itself.
--
-- The data is the same at the end as after sql/seed.sql (the demo rows
-- are deleted at the end, see "Clean up").
--
-- Run (errors are expected, so use --force):
--   scripts/mysql.sh run --force sql/tx_ingest_demo.sql
-- or, against another database name:
--   DB=dephgraph_tx scripts/tx_two_sessions.sh ingest
-- Output: logs/tx_ingest_demo.log
-- =====================================================================

USE dephgraph;

SELECT 'before the demo' AS state,
       (SELECT COUNT(*) FROM deployment)  AS deployments,
       (SELECT COUNT(*) FROM resolves_to) AS resolves_to_rows;

-- =====================================================================
-- (a) Failure half-way -> ROLLBACK
--     New lockfile for payments-svc (service 10) becomes deployment 3.
--     The lockfile names "log4j-core 2.99.0", which is not in
--     PACKAGE_VERSION, so the FK fk_rt_version rejects that row.
-- =====================================================================

SELECT '(a) ingest with a bad row' AS demo,
       'ERROR 1452 on the bad row, then ROLLBACK; nothing stored' AS expected;

START TRANSACTION;

INSERT INTO deployment (service_id, deploy_seq, environment, deployed_at, is_active)
VALUES (10, 3, 'PROD', '2026-11-01 10:00:00', TRUE);

-- statement 2: two good rows
INSERT INTO resolves_to (service_id, deploy_seq, package_id, version_string, depth)
VALUES (10, 3, 102, '2.5.0', 0),
       (10, 3, 101, '1.2.3', 0);

-- statement 3: one good row and one bad row -> the whole statement fails
INSERT INTO resolves_to (service_id, deploy_seq, package_id, version_string, depth)
VALUES (10, 3, 100, '2.17.1', 1),
       (10, 3, 100, '2.99.0', 1);

-- The transaction is still open. Statements 1 and 2 are still in it;
-- statement 3 was undone as a whole (neither of its two rows is here).
SELECT 'inside the open transaction, after the error' AS state,
       (SELECT COUNT(*) FROM deployment  WHERE service_id = 10 AND deploy_seq = 3) AS deployment_3,
       (SELECT COUNT(*) FROM resolves_to WHERE service_id = 10 AND deploy_seq = 3) AS resolves_to_rows_3;

-- The client sees the error and undoes the whole ingest.
ROLLBACK;

SELECT 'after ROLLBACK' AS state,
       (SELECT COUNT(*) FROM deployment  WHERE service_id = 10 AND deploy_seq = 3) AS deployment_3,
       (SELECT COUNT(*) FROM resolves_to WHERE service_id = 10 AND deploy_seq = 3) AS resolves_to_rows_3;

-- =====================================================================
-- (b) All rows fine -> COMMIT
--     Same lockfile, fixed: log4j-core is now 2.17.1 (the upgrade from
--     remediation plan 50).
-- =====================================================================

SELECT '(b) ingest with good rows' AS demo,
       'COMMIT; 1 deployment and 3 resolves_to rows stored' AS expected;

START TRANSACTION;

INSERT INTO deployment (service_id, deploy_seq, environment, deployed_at, is_active)
VALUES (10, 3, 'PROD', '2026-11-01 10:00:00', TRUE);

INSERT INTO resolves_to (service_id, deploy_seq, package_id, version_string, depth)
VALUES (10, 3, 102, '2.5.0',  0),
       (10, 3, 101, '1.2.3',  0),
       (10, 3, 100, '2.17.1', 1);

COMMIT;

SELECT rt.service_id, rt.deploy_seq, d.environment, p.package_name, rt.version_string, rt.depth
FROM   resolves_to rt
JOIN   deployment  d ON d.service_id = rt.service_id AND d.deploy_seq = rt.deploy_seq
JOIN   package     p ON p.package_id = rt.package_id
WHERE  rt.service_id = 10 AND rt.deploy_seq = 3
ORDER  BY rt.depth, p.package_name;

-- Durability check from the same data: deployment 3 no longer resolves
-- the Log4Shell version, so it has no open AFFECTS rows.
SELECT 'vulnerable rows in deployment 3' AS check_name, COUNT(*) AS found
FROM   resolves_to rt
JOIN   affects a ON a.package_id = rt.package_id AND a.version_string = rt.version_string
WHERE  rt.service_id = 10 AND rt.deploy_seq = 3;

-- =====================================================================
-- (c) SAVEPOINT: undo only part of a transaction
--     Deployment 4: direct dependencies are stored first, then the
--     transitive ones. The first try at the transitive rows has a bad
--     version, so we go back to the savepoint and try again, without
--     losing the deployment row or the direct dependencies.
-- =====================================================================

SELECT '(c) SAVEPOINT and ROLLBACK TO SAVEPOINT' AS demo,
       'bad transitive rows undone, direct rows kept, COMMIT' AS expected;

START TRANSACTION;

INSERT INTO deployment (service_id, deploy_seq, environment, deployed_at, is_active)
VALUES (10, 4, 'STAGING', '2026-11-02 09:00:00', TRUE);

INSERT INTO resolves_to (service_id, deploy_seq, package_id, version_string, depth)
VALUES (10, 4, 102, '2.5.0', 0),
       (10, 4, 101, '1.2.3', 0);

SAVEPOINT direct_deps_done;

-- first try: one good transitive row ...
INSERT INTO resolves_to (service_id, deploy_seq, package_id, version_string, depth)
VALUES (10, 4, 100, '2.14.1', 1);
-- ... and one bad one (version not in PACKAGE_VERSION)
INSERT INTO resolves_to (service_id, deploy_seq, package_id, version_string, depth)
VALUES (10, 4, 100, '2.99.0', 2);

SELECT 'before ROLLBACK TO SAVEPOINT' AS state,
       COUNT(*) AS resolves_to_rows_4
FROM   resolves_to WHERE service_id = 10 AND deploy_seq = 4;

ROLLBACK TO SAVEPOINT direct_deps_done;

SELECT 'after ROLLBACK TO SAVEPOINT' AS state,
       (SELECT COUNT(*) FROM deployment  WHERE service_id = 10 AND deploy_seq = 4) AS deployment_4,
       (SELECT COUNT(*) FROM resolves_to WHERE service_id = 10 AND deploy_seq = 4) AS resolves_to_rows_4;

-- second try with the fixed version, then commit everything
INSERT INTO resolves_to (service_id, deploy_seq, package_id, version_string, depth)
VALUES (10, 4, 100, '2.17.1', 1);

COMMIT;

SELECT rt.deploy_seq, p.package_name, rt.version_string, rt.depth
FROM   resolves_to rt JOIN package p ON p.package_id = rt.package_id
WHERE  rt.service_id = 10 AND rt.deploy_seq = 4
ORDER  BY rt.depth, p.package_name;

-- =====================================================================
-- (d) autocommit
--     With autocommit = 1 (the MySQL default) every statement outside
--     START TRANSACTION is a transaction of its own and is committed at
--     once; a later ROLLBACK cannot undo it.
--     With autocommit = 0 a transaction starts by itself at the first
--     statement and lasts until COMMIT or ROLLBACK.
-- =====================================================================

SELECT '(d) autocommit' AS demo,
       'autocommit=1: ROLLBACK cannot undo; autocommit=0: it can' AS expected;

SELECT @@autocommit AS autocommit_now;

-- d1: autocommit = 1
INSERT INTO deployment (service_id, deploy_seq, environment, deployed_at, is_active)
VALUES (10, 5, 'DEV', '2026-11-03 09:00:00', TRUE);
ROLLBACK;
SELECT 'autocommit=1, INSERT then ROLLBACK' AS state,
       COUNT(*) AS deployment_5_rows
FROM   deployment WHERE service_id = 10 AND deploy_seq = 5;

-- d2: a single multi-row statement is still all-or-nothing under autocommit
INSERT INTO resolves_to (service_id, deploy_seq, package_id, version_string, depth)
VALUES (10, 5, 102, '2.5.0',  0),
       (10, 5, 100, '2.99.0', 1);
SELECT 'autocommit=1, one INSERT of 2 rows, 1 bad' AS state,
       COUNT(*) AS resolves_to_rows_5
FROM   resolves_to WHERE service_id = 10 AND deploy_seq = 5;

-- d3: autocommit = 0
SET autocommit = 0;
SELECT @@autocommit AS autocommit_now;
INSERT INTO deployment (service_id, deploy_seq, environment, deployed_at, is_active)
VALUES (10, 6, 'DEV', '2026-11-04 09:00:00', TRUE);
ROLLBACK;
SELECT 'autocommit=0, INSERT then ROLLBACK' AS state,
       COUNT(*) AS deployment_6_rows
FROM   deployment WHERE service_id = 10 AND deploy_seq = 6;
SET autocommit = 1;

-- =====================================================================
-- Clean up: remove the demo deployments 3, 4, 5.
-- ON DELETE CASCADE on fk_rt_deployment removes their RESOLVES_TO rows.
-- =====================================================================

DELETE FROM deployment WHERE service_id = 10 AND deploy_seq >= 3;

SELECT 'after the demo (same as seed: 2 and 3)' AS state,
       (SELECT COUNT(*) FROM deployment)  AS deployments,
       (SELECT COUNT(*) FROM resolves_to) AS resolves_to_rows;
