-- Dirty read, session B: OSV sync writes a wrong severity for Log4Shell
-- (MEDIUM, 5.0), does not commit, and later rolls back.
DO SLEEP(GREATEST(0, @t0 + 0.0 - UNIX_TIMESTAMP(NOW(6))));
START TRANSACTION;
UPDATE vulnerability SET severity = 'MEDIUM', cvss_score = 5.0 WHERE vuln_id = 900;
SELECT 'B' AS session, 'changed 900 to MEDIUM 5.0, NOT committed' AS step;
DO SLEEP(GREATEST(0, @t0 + 3.0 - UNIX_TIMESTAMP(NOW(6))));
ROLLBACK;
SELECT 'B' AS session, 'rolled back: 900 is CRITICAL 10.0 again' AS step;
