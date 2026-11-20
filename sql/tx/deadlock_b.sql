-- Deadlock, session B: remediation planner.
-- Locks the vulnerability first (Y), then the AFFECTS row (X), with
-- locking reads. Order: Y then X, the opposite of A -> deadlock.
-- B has changed no rows, so it is the cheaper transaction to undo:
-- InnoDB picks B as the victim. B then retries, as an application should.
DO SLEEP(GREATEST(0, @t0 + 1.0 - UNIX_TIMESTAMP(NOW(6))));
START TRANSACTION;
SELECT vuln_id, severity, cvss_score FROM vulnerability WHERE vuln_id = 900 FOR UPDATE;
DO SLEEP(GREATEST(0, @t0 + 3.0 - UNIX_TIMESTAMP(NOW(6))));
SELECT fixed_in FROM affects
WHERE  vuln_id = 900 AND package_id = 100 AND version_string = '2.14.1'
FOR UPDATE;
SELECT 'B' AS session, 'after ERROR 1213: whole transaction rolled back, locks held now' AS step,
       COUNT(*) AS locks_held
FROM   performance_schema.data_locks
WHERE  THREAD_ID = PS_CURRENT_THREAD_ID();
SELECT 'B' AS session, 'retry the whole transaction' AS step;
SET @asked = NOW(6);
START TRANSACTION;
SELECT vuln_id, severity, cvss_score FROM vulnerability WHERE vuln_id = 900 FOR UPDATE;
SELECT fixed_in FROM affects
WHERE  vuln_id = 900 AND package_id = 100 AND version_string = '2.14.1'
FOR UPDATE;
COMMIT;
SELECT 'B' AS session, 'retry committed' AS step,
       CAST(TIMESTAMPDIFF(MICROSECOND, @asked, NOW(6)) / 1000000 AS DECIMAL(5,2)) AS retry_waited_s;
