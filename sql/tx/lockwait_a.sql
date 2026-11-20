-- Lock wait, session A: remediation planner.
-- Locks the Log4Shell AFFECTS row so fixed_in cannot change while it
-- builds a plan, holds the lock for 4 seconds, then commits.
DO SLEEP(GREATEST(0, @t0 + 0.0 - UNIX_TIMESTAMP(NOW(6))));
SELECT 'A' AS session, 'planner: lock AFFECTS (900,100,2.14.1) FOR UPDATE' AS step;
START TRANSACTION;
SELECT vuln_id, package_id, version_string, fixed_in
FROM   affects
WHERE  vuln_id = 900 AND package_id = 100 AND version_string = '2.14.1'
FOR UPDATE;
DO SLEEP(GREATEST(0, @t0 + 4.0 - UNIX_TIMESTAMP(NOW(6))));
SELECT 'A' AS session, 'planner: plan done, COMMIT (releases the lock)' AS step;
COMMIT;
DO SLEEP(GREATEST(0, @t0 + 5.0 - UNIX_TIMESTAMP(NOW(6))));
SELECT 'A' AS session, 'row after both sessions ended (B rolled back)' AS step,
       fixed_in
FROM   affects
WHERE  vuln_id = 900 AND package_id = 100 AND version_string = '2.14.1';
