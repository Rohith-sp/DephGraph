-- Dirty read, session A: a dashboard reads vulnerability 900 at t = 1 s
-- (while B's change is uncommitted) under three isolation levels, and
-- once more at t = 4 s (after B rolled back).
-- Each SELECT runs in its own autocommit transaction.
DO SLEEP(GREATEST(0, @t0 + 1.0 - UNIX_TIMESTAMP(NOW(6))));
SET SESSION TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;
SELECT @@transaction_isolation AS isolation, severity, cvss_score FROM vulnerability WHERE vuln_id = 900;
SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED;
SELECT @@transaction_isolation AS isolation, severity, cvss_score FROM vulnerability WHERE vuln_id = 900;
SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ;
SELECT @@transaction_isolation AS isolation, severity, cvss_score FROM vulnerability WHERE vuln_id = 900;
DO SLEEP(GREATEST(0, @t0 + 4.0 - UNIX_TIMESTAMP(NOW(6))));
SET SESSION TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;
SELECT @@transaction_isolation AS isolation, severity, cvss_score FROM vulnerability WHERE vuln_id = 900;
