-- =====================================================================
-- DephGraph — Phase 5: prove database rollback on a mid-transaction error
--
-- Run after er-diagram/schema.sql:
--   scripts/mysql.sh run --force sql/transaction_demo.sql
--
-- The deliberate FK error is expected. --force lets the client continue
-- to ROLLBACK and print the final counts. No sample row is changed.
-- =====================================================================

USE dephgraph;

SELECT 'before' AS checkpoint,
       (SELECT COUNT(*) FROM package) AS packages,
       (SELECT COUNT(*) FROM package_version) AS versions,
       (SELECT COUNT(*) FROM affects) AS affected_rows;

START TRANSACTION;

INSERT INTO package
    (package_id, package_name, ecosystem, is_internal)
VALUES
    (990001, 'rollback-demo-only', 'Maven', TRUE);

INSERT INTO package_version (package_id, version_string, published_at)
VALUES (990001, '0.0.1', CURRENT_DATE);

-- Deliberate failure: vulnerability 999999 does not exist. This simulates
-- a failure after earlier writes have succeeded in the same transaction.
INSERT INTO affects (vuln_id, package_id, version_string, fixed_in)
VALUES (999999, 990001, '0.0.1', '0.0.2');

ROLLBACK;

SELECT 'after rollback (expected unchanged)' AS checkpoint,
       (SELECT COUNT(*) FROM package) AS packages,
       (SELECT COUNT(*) FROM package_version) AS versions,
       (SELECT COUNT(*) FROM affects) AS affected_rows;

SELECT COUNT(*) AS rollback_demo_rows_remaining
FROM package
WHERE package_id = 990001;
