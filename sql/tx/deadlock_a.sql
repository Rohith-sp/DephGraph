-- Deadlock, session A: OSV sync.
-- A new advisory revision arrives: A changes fixed_in of the AFFECTS row
-- (X lock on AFFECTS row X), then the CVSS score of the vulnerability
-- (X lock on VULNERABILITY row Y). Order: X then Y.
-- A rolls back at the end so the sample data does not change.
DO SLEEP(GREATEST(0, @t0 + 0.0 - UNIX_TIMESTAMP(NOW(6))));
START TRANSACTION;
UPDATE affects SET fixed_in = '2.17.1'
WHERE  vuln_id = 900 AND package_id = 100 AND version_string = '2.14.1';
DO SLEEP(GREATEST(0, @t0 + 2.0 - UNIX_TIMESTAMP(NOW(6))));
SET @asked = NOW(6);
UPDATE vulnerability SET cvss_score = 9.8 WHERE vuln_id = 900;
SELECT 'A' AS session, 'got VULNERABILITY 900 after B was rolled back' AS step,
       CAST(TIMESTAMPDIFF(MICROSECOND, @asked, NOW(6)) / 1000000 AS DECIMAL(5,2)) AS waited_s;
SELECT a.fixed_in, v.cvss_score
FROM   affects a JOIN vulnerability v ON v.vuln_id = a.vuln_id
WHERE  a.vuln_id = 900 AND a.package_id = 100 AND a.version_string = '2.14.1';
DO SLEEP(GREATEST(0, @t0 + 4.0 - UNIX_TIMESTAMP(NOW(6))));
ROLLBACK;
