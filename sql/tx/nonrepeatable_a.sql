-- Non-repeatable read and phantom, session A: remediation planner under READ COMMITTED.
-- Reads row X (fixed_in of AFFECTS (900,100,2.14.1)) and counts the AFFECTS rows
-- of vulnerability 900, twice in ONE transaction. B commits a change in between.
DO SLEEP(GREATEST(0, @t0 + 0.0 - UNIX_TIMESTAMP(NOW(6))));
SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED;
START TRANSACTION;
SELECT 'A' AS session, @@transaction_isolation AS isolation, 'first read' AS step;
SELECT fixed_in FROM affects WHERE vuln_id = 900 AND package_id = 100 AND version_string = '2.14.1';
SELECT COUNT(*) AS rows_for_vuln_900 FROM affects WHERE vuln_id = 900;
DO SLEEP(GREATEST(0, @t0 + 2.0 - UNIX_TIMESTAMP(NOW(6))));
SELECT 'A' AS session, @@transaction_isolation AS isolation, 'second read, same transaction' AS step;
SELECT fixed_in FROM affects WHERE vuln_id = 900 AND package_id = 100 AND version_string = '2.14.1';
SELECT COUNT(*) AS rows_for_vuln_900 FROM affects WHERE vuln_id = 900;
COMMIT;
