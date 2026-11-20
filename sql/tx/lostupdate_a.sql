-- Lost update, session A: engineer 1 adds 2 hours to the estimate of
-- remediation action (50, 1) (log4j-core upgrade, 4.0 h in the seed).
-- Part 1 (t = 0..1): read with a plain SELECT, write back read value + 2.
-- Part 2 (t = 2.5..3.5): same, but the read is SELECT ... FOR UPDATE.
DO SLEEP(GREATEST(0, @t0 + 0.0 - UNIX_TIMESTAMP(NOW(6))));
START TRANSACTION;
SELECT est_effort_hrs FROM remediation_action WHERE plan_id = 50 AND action_seq = 1 INTO @e;
SELECT 'A' AS session, 'part 1: plain read' AS step, @e AS read_hrs;
DO SLEEP(GREATEST(0, @t0 + 1.0 - UNIX_TIMESTAMP(NOW(6))));
UPDATE remediation_action SET est_effort_hrs = @e + 2 WHERE plan_id = 50 AND action_seq = 1;
COMMIT;
SELECT 'A' AS session, 'part 1: wrote read + 2, committed' AS step, est_effort_hrs AS now_hrs FROM remediation_action WHERE plan_id = 50 AND action_seq = 1;
DO SLEEP(GREATEST(0, @t0 + 2.5 - UNIX_TIMESTAMP(NOW(6))));
START TRANSACTION;
SELECT est_effort_hrs FROM remediation_action WHERE plan_id = 50 AND action_seq = 1 FOR UPDATE INTO @e;
SELECT 'A' AS session, 'part 2: read FOR UPDATE' AS step, @e AS read_hrs;
DO SLEEP(GREATEST(0, @t0 + 3.5 - UNIX_TIMESTAMP(NOW(6))));
UPDATE remediation_action SET est_effort_hrs = @e + 2 WHERE plan_id = 50 AND action_seq = 1;
COMMIT;
SELECT 'A' AS session, 'part 2: wrote read + 2, committed' AS step, est_effort_hrs AS now_hrs FROM remediation_action WHERE plan_id = 50 AND action_seq = 1;
