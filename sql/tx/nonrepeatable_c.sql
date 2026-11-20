-- Non-repeatable read and phantom, session C: remediation planner under REPEATABLE READ.
-- Reads row X (fixed_in of AFFECTS (900,100,2.14.1)) and counts the AFFECTS rows
-- of vulnerability 900, twice in ONE transaction. B commits a change in between.
DO SLEEP(GREATEST(0, @t0 + 0.5 - UNIX_TIMESTAMP(NOW(6))));
SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ;
START TRANSACTION;
SELECT 'C' AS session, @@transaction_isolation AS isolation, 'first read' AS step;
SELECT fixed_in FROM affects WHERE vuln_id = 900 AND package_id = 100 AND version_string = '2.14.1';
SELECT COUNT(*) AS rows_for_vuln_900 FROM affects WHERE vuln_id = 900;
DO SLEEP(GREATEST(0, @t0 + 2.5 - UNIX_TIMESTAMP(NOW(6))));
SELECT 'C' AS session, @@transaction_isolation AS isolation, 'second read, same transaction' AS step;
SELECT fixed_in FROM affects WHERE vuln_id = 900 AND package_id = 100 AND version_string = '2.14.1';
SELECT COUNT(*) AS rows_for_vuln_900 FROM affects WHERE vuln_id = 900;
-- A locking read is a "current read": it reads the newest committed row, not the snapshot.
SELECT 'C' AS session, 'locking read (FOR SHARE) in the same transaction' AS step;
SELECT fixed_in FROM affects WHERE vuln_id = 900 AND package_id = 100 AND version_string = '2.14.1' FOR SHARE;
COMMIT;
