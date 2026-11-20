-- Lost update, session B: engineer 2 also adds 2 hours, at the same time.
-- Both parts should end at 4.0 + 2 + 2 = 8.0 hours.
-- Between the parts, and at the end, B puts the seed value 4.0 back.
DO SLEEP(GREATEST(0, @t0 + 0.5 - UNIX_TIMESTAMP(NOW(6))));
START TRANSACTION;
SELECT est_effort_hrs FROM remediation_action WHERE plan_id = 50 AND action_seq = 1 INTO @e;
SELECT 'B' AS session, 'part 1: plain read' AS step, @e AS read_hrs;
DO SLEEP(GREATEST(0, @t0 + 1.5 - UNIX_TIMESTAMP(NOW(6))));
UPDATE remediation_action SET est_effort_hrs = @e + 2 WHERE plan_id = 50 AND action_seq = 1;
COMMIT;
SELECT 'B' AS session, 'part 1: result (8.0 expected, A''s +2 is lost)' AS step, est_effort_hrs AS now_hrs FROM remediation_action WHERE plan_id = 50 AND action_seq = 1;
UPDATE remediation_action SET est_effort_hrs = 4.0 WHERE plan_id = 50 AND action_seq = 1;
DO SLEEP(GREATEST(0, @t0 + 3.0 - UNIX_TIMESTAMP(NOW(6))));
START TRANSACTION;
SET @asked = NOW(6);
SELECT est_effort_hrs FROM remediation_action WHERE plan_id = 50 AND action_seq = 1 FOR UPDATE INTO @e;
SELECT 'B' AS session, 'part 2: read FOR UPDATE (waited for A)' AS step, @e AS read_hrs,
       CAST(TIMESTAMPDIFF(MICROSECOND, @asked, NOW(6)) / 1000000 AS DECIMAL(5,2)) AS waited_s;
UPDATE remediation_action SET est_effort_hrs = @e + 2 WHERE plan_id = 50 AND action_seq = 1;
COMMIT;
SELECT 'B' AS session, 'part 2: result (8.0 expected)' AS step, est_effort_hrs AS now_hrs FROM remediation_action WHERE plan_id = 50 AND action_seq = 1;
DO SLEEP(GREATEST(0, @t0 + 4.5 - UNIX_TIMESTAMP(NOW(6))));
UPDATE remediation_action SET est_effort_hrs = 4.0 WHERE plan_id = 50 AND action_seq = 1;
SELECT 'B' AS session, 'put back the seed value' AS step, est_effort_hrs AS now_hrs FROM remediation_action WHERE plan_id = 50 AND action_seq = 1;
