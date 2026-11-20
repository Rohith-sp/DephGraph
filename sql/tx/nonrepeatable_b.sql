-- Non-repeatable read and phantom, session B: OSV sync.
-- At t = 1 s it commits one transaction that changes row X and inserts a
-- new AFFECTS row for vulnerability 900 (an illustrative extra affected
-- version). At t = 3 s it undoes both with a compensating transaction.
DO SLEEP(GREATEST(0, @t0 + 1.0 - UNIX_TIMESTAMP(NOW(6))));
START TRANSACTION;
UPDATE affects SET fixed_in = '2.17.1'
WHERE  vuln_id = 900 AND package_id = 100 AND version_string = '2.14.1';
INSERT INTO affects (vuln_id, package_id, version_string, fixed_in)
VALUES (900, 102, '2.5.0', '2.6.2');
COMMIT;
SELECT 'B' AS session, 'committed: fixed_in 2.17.1 and one new AFFECTS row' AS step;
DO SLEEP(GREATEST(0, @t0 + 3.0 - UNIX_TIMESTAMP(NOW(6))));
START TRANSACTION;
UPDATE affects SET fixed_in = '2.15.0'
WHERE  vuln_id = 900 AND package_id = 100 AND version_string = '2.14.1';
DELETE FROM affects WHERE vuln_id = 900 AND package_id = 102 AND version_string = '2.5.0';
COMMIT;
SELECT 'B' AS session, 'compensating transaction committed: data as seeded' AS step;
