-- Lock wait, session B: OSV sync wants to change fixed_in of the same row.
-- Try 1: innodb_lock_wait_timeout = 1 -> gives up with ERROR 1205.
-- Try 2: innodb_lock_wait_timeout = 10 -> waits until A commits.
-- B rolls back at the end so the sample data does not change.
DO SLEEP(GREATEST(0, @t0 + 0.5 - UNIX_TIMESTAMP(NOW(6))));
SET SESSION innodb_lock_wait_timeout = 1;
START TRANSACTION;
SET @asked = NOW(6);
UPDATE affects SET fixed_in = '2.17.1'
WHERE  vuln_id = 900 AND package_id = 100 AND version_string = '2.14.1';
SELECT 'B' AS session, 'try 1 gave up' AS step,
       CAST(TIMESTAMPDIFF(MICROSECOND, @asked, NOW(6)) / 1000000 AS DECIMAL(5,2)) AS waited_s;
DO SLEEP(GREATEST(0, @t0 + 2.0 - UNIX_TIMESTAMP(NOW(6))));
SET SESSION innodb_lock_wait_timeout = 10;
SET @asked = NOW(6);
UPDATE affects SET fixed_in = '2.17.1'
WHERE  vuln_id = 900 AND package_id = 100 AND version_string = '2.14.1';
SELECT 'B' AS session, 'try 2 got the lock' AS step,
       CAST(TIMESTAMPDIFF(MICROSECOND, @asked, NOW(6)) / 1000000 AS DECIMAL(5,2)) AS waited_s;
SELECT fixed_in AS fixed_in_seen_by_B
FROM   affects
WHERE  vuln_id = 900 AND package_id = 100 AND version_string = '2.14.1';
ROLLBACK;
