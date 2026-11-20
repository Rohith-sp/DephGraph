-- =====================================================================
-- DephGraph — sample data (MySQL 8.4)
--
-- Loads exactly the Phase 2 sample rows (relational-model/README.md,
-- "The tables with sample rows"): payments-svc pulls in the vulnerable
-- log4j-core 2.14.1 (Log4Shell) through spring-boot-starter-log4j2.
--
-- The constraint cases (I1-U3) and queries (Q1-Q7) in
-- relational-algebra/README.md were hand-checked on these rows, so
-- running them against this data must give the documented results.
--
-- Columns the Phase 2 tables leave out (summaries, publish dates,
-- hashes, waiver reason, ...) are filled with plausible values.
-- All data is made up for illustration.
--
-- Run after schema.sql:   scripts/mysql.sh run sql/seed.sql
-- Safe to re-run: it empties every table first.
-- =====================================================================

USE dephgraph;

-- ---------------------------------------------------------------------
-- Start from empty tables (FK checks off only while truncating)
-- ---------------------------------------------------------------------
SET FOREIGN_KEY_CHECKS = 0;
TRUNCATE TABLE pr_risk_verdict;
TRUNCATE TABLE pr_review;
TRUNCATE TABLE resolves;
TRUNCATE TABLE remediation_action;
TRUNCATE TABLE remediation_plan;
TRUNCATE TABLE waived_for;
TRUNCATE TABLE affects;
TRUNCATE TABLE vulnerability;
TRUNCATE TABLE observes;
TRUNCATE TABLE scan_run;
TRUNCATE TABLE resolves_to;
TRUNCATE TABLE depends_on;
TRUNCATE TABLE maintained_by;
TRUNCATE TABLE maintainer_email;
TRUNCATE TABLE maintainer;
TRUNCATE TABLE package_version;
TRUNCATE TABLE package_alias;
TRUNCATE TABLE package;
TRUNCATE TABLE semantic_cluster;
TRUNCATE TABLE license;
TRUNCATE TABLE deployment;
TRUNCATE TABLE service;
TRUNCATE TABLE team;
SET FOREIGN_KEY_CHECKS = 1;

-- All inserts in one transaction: either the whole sample loads or none of it.
START TRANSACTION;

-- ---------------------------------------------------------------------
-- Organisation layer
-- ---------------------------------------------------------------------
-- 1. TEAM
INSERT INTO team (team_id, team_name, contact_email, contact_slack) VALUES
  (1, 'Payments', 'payments@example.com', '#payments'),
  (2, 'Platform', 'platform@example.com', '#platform');

-- 2. SERVICE (OWNS: every service has exactly one team)
INSERT INTO service (service_id, service_name, repo_url, criticality, team_id) VALUES
  (10, 'payments-svc', 'https://git.example.com/payments/payments-svc', 'HIGH',     1),
  (11, 'auth-svc',     'https://git.example.com/platform/auth-svc',     'CRITICAL', 2);

-- 10. DEPLOYMENT (weak entity, owner SERVICE)
INSERT INTO deployment (service_id, deploy_seq, environment, deployed_at, is_active) VALUES
  (10, 1, 'PROD',    '2026-09-01 10:00:00', TRUE),
  (10, 2, 'STAGING', '2026-10-05 16:30:00', TRUE);

-- ---------------------------------------------------------------------
-- Package catalogue
-- ---------------------------------------------------------------------
-- 5. LICENSE
INSERT INTO license (license_id, spdx_id, license_name, is_copyleft, is_permissive) VALUES
  (1, 'Apache-2.0',    'Apache License 2.0',                         FALSE, TRUE),
  (2, 'LGPL-2.1-only', 'GNU Lesser General Public License v2.1 only', TRUE,  FALSE);

-- 7. SEMANTIC_CLUSTER (before PACKAGE: package.cluster_id references it)
INSERT INTO semantic_cluster (cluster_id, cluster_label, representative_package_id, cohesion_score) VALUES
  (3, 'logging',     100,  0.870),
  (4, 'http client', NULL, NULL);

-- 4. PACKAGE (GROUPED_INTO is partial: 102 has no cluster)
INSERT INTO package (package_id, package_name, ecosystem, source_repo_url, cluster_id) VALUES
  (100, 'log4j-core',                 'Maven', 'https://github.com/apache/logging-log4j2',  3),
  (101, 'logback-classic',            'Maven', 'https://github.com/qos-ch/logback',         3),
  (102, 'spring-boot-starter-log4j2', 'Maven', 'https://github.com/spring-projects/spring-boot', NULL);

-- 21. PACKAGE_ALIAS (multivalued attribute package.alias)
INSERT INTO package_alias (package_id, alias) VALUES
  (100, 'log4j2'),
  (100, 'org.apache.logging.log4j:log4j-core');

-- 11. PACKAGE_VERSION (weak entity, owner PACKAGE); sha256 is a stand-in hash
INSERT INTO package_version (package_id, version_string, published_at, license_id, sha256) VALUES
  (100, '2.14.1', '2021-03-06', 1, SHA2('log4j-core@2.14.1', 256)),
  (100, '2.17.1', '2021-12-27', 1, SHA2('log4j-core@2.17.1', 256)),
  (101, '1.2.3',  '2017-03-31', 2, SHA2('logback-classic@1.2.3', 256)),
  (102, '2.5.0',  '2021-05-20', 1, SHA2('spring-boot-starter-log4j2@2.5.0', 256));

-- 6. MAINTAINER
INSERT INTO maintainer (maintainer_id, handle, display_name, last_active_at) VALUES
  (200, 'mdev',   'M. Dev',   '2026-10-15'),
  (201, 'kpatel', 'K. Patel', '2025-08-02');

-- 22. MAINTAINER_EMAIL (multivalued attribute maintainer.email)
INSERT INTO maintainer_email (maintainer_id, email) VALUES
  (200, 'mdev@example.com'),
  (200, 'mdev@example.org'),
  (201, 'kpatel@example.com');

-- 17. MAINTAINED_BY (M:N)
INSERT INTO maintained_by (package_id, maintainer_id, role, since_date) VALUES
  (100, 200, 'OWNER', '2019-01-10'),
  (101, 201, 'OWNER', '2016-06-01');

-- ---------------------------------------------------------------------
-- Dependency graph
-- ---------------------------------------------------------------------
-- 16. DEPENDS_ON (recursive M:N on PACKAGE_VERSION)
INSERT INTO depends_on (dependent_package_id, dependent_version,
                        dependency_package_id, dependency_version, scope, is_direct) VALUES
  (102, '2.5.0', 100, '2.14.1', 'runtime', TRUE);

-- 15. RESOLVES_TO (what the PROD deployment's lockfile resolved to)
INSERT INTO resolves_to (service_id, deploy_seq, package_id, version_string, depth, resolved_at) VALUES
  (10, 1, 102, '2.5.0',  0, '2026-09-01 10:00:00'),
  (10, 1, 100, '2.14.1', 1, '2026-09-01 10:00:00'),
  (10, 1, 101, '1.2.3',  0, '2026-09-01 10:00:00');

-- ---------------------------------------------------------------------
-- Runtime evidence (ternary OBSERVES)
-- ---------------------------------------------------------------------
-- 3. SCAN_RUN
INSERT INTO scan_run (scan_id, source_tool, started_at, ended_at) VALUES
  (500, 'ebpf-agent', '2026-08-01 00:00:00', '2026-08-31 00:00:00'),
  (501, 'osv-sync',   '2026-09-01 02:00:00', '2026-09-01 02:04:00');

-- 23. OBSERVES (is_live is generated from exec_count)
INSERT INTO observes (service_id, deploy_seq, package_id, version_string, scan_id,
                      exec_count, first_seen, last_seen) VALUES
  (10, 1, 100, '2.14.1', 500, 1840, '2026-08-01 00:05:00', '2026-08-30 23:41:00'),
  (10, 1, 101, '1.2.3',  500, 0,    NULL,                  NULL);

-- ---------------------------------------------------------------------
-- Vulnerabilities and remediation
-- ---------------------------------------------------------------------
-- 8. VULNERABILITY
INSERT INTO vulnerability (vuln_id, osv_id, summary, severity, cvss_score, published_at) VALUES
  (900, 'GHSA-jfh8-c2jp-5v3q', 'Remote code injection in Log4j (Log4Shell, CVE-2021-44228)',
        'CRITICAL', 10.0, '2021-12-10'),
  (901, 'GHSA-7rjr-3q55-vv33', 'Incomplete fix for Log4Shell in certain non-default configurations (CVE-2021-45046)',
        'CRITICAL', 9.0,  '2021-12-14');

-- 18. AFFECTS (fixed_in depends on the vulnerability as well as the version)
INSERT INTO affects (vuln_id, package_id, version_string, fixed_in) VALUES
  (900, 100, '2.14.1', '2.15.0'),
  (901, 100, '2.14.1', '2.16.0');

-- 20. WAIVED_FOR
INSERT INTO waived_for (vuln_id, service_id, reason, approved_by, expires_on) VALUES
  (901, 10, 'Message lookups disabled in payments-svc config; upgrade scheduled in plan 50',
        'security-lead', '2026-11-30');

-- 9. REMEDIATION_PLAN (RAISED_BY: a team raises plans)
INSERT INTO remediation_plan (plan_id, team_id, created_at, status) VALUES
  (50, 1, '2026-10-01 09:00:00', 'IN_PROGRESS'),
  (51, 2, '2026-10-12 09:00:00', 'DRAFT');

-- 12. REMEDIATION_ACTION (weak entity, owner REMEDIATION_PLAN)
INSERT INTO remediation_action (plan_id, action_seq, action_type, target_version, est_effort_hrs) VALUES
  (50, 1, 'UPGRADE', '2.17.1', 4.0),
  (50, 2, 'REMOVE',  NULL,     1.5);

-- 19. RESOLVES (M:N: one action clears two vulnerabilities)
INSERT INTO resolves (plan_id, action_seq, vuln_id) VALUES
  (50, 1, 900),
  (50, 1, 901);

-- ---------------------------------------------------------------------
-- PR risk gate
-- ---------------------------------------------------------------------
-- 13. PR_REVIEW (weak entity, owner SERVICE)
INSERT INTO pr_review (service_id, pr_number, title, author, opened_at, merged_at, filter_stage_exit) VALUES
  (10, 42, 'Bump logging dependencies',    'alice', '2026-10-20 11:05:00', NULL, 'STAGE_2'),
  (11, 7,  'Update README badges',         'bob',   '2026-10-21 09:40:00', '2026-10-21 10:02:00', 'STAGE_1');

-- 14. PR_RISK_VERDICT (weak entity, owner PR_REVIEW); no LLM below STAGE_3, so model_used is NULL
INSERT INTO pr_risk_verdict (service_id, pr_number, dep_name, dep_ecosystem, dep_version, verdict, reason,
                             cve_count_seen, abandonment_seen, siblings_seen, context_tokens, model_used,
                             decided_at) VALUES
  (10, 42, 'log4j-core', 'Maven', '2.14.1', 'RISK',    'CVSS 10.0, fix available in 2.17.1',
        2, 12.0, 1, NULL, NULL, '2026-10-20 11:06:00'),
  (10, 42, 'gson',       'Maven', '2.10.1', 'APPROVE', 'no known vulnerabilities',
        0, 8.5,  0, NULL, NULL, '2026-10-20 11:06:00');

COMMIT;

-- ---------------------------------------------------------------------
-- Check: row count per table (expected values from Phase 2)
-- ---------------------------------------------------------------------
SELECT t.tbl AS table_name, t.expected, t.found,
       IF(t.expected = t.found, 'ok', 'MISMATCH') AS result
FROM (
            SELECT 'team'               AS tbl, 2 AS expected, (SELECT COUNT(*) FROM team)               AS found
  UNION ALL SELECT 'service',                   2, (SELECT COUNT(*) FROM service)
  UNION ALL SELECT 'deployment',                2, (SELECT COUNT(*) FROM deployment)
  UNION ALL SELECT 'license',                   2, (SELECT COUNT(*) FROM license)
  UNION ALL SELECT 'semantic_cluster',          2, (SELECT COUNT(*) FROM semantic_cluster)
  UNION ALL SELECT 'package',                   3, (SELECT COUNT(*) FROM package)
  UNION ALL SELECT 'package_alias',             2, (SELECT COUNT(*) FROM package_alias)
  UNION ALL SELECT 'package_version',           4, (SELECT COUNT(*) FROM package_version)
  UNION ALL SELECT 'maintainer',                2, (SELECT COUNT(*) FROM maintainer)
  UNION ALL SELECT 'maintainer_email',          3, (SELECT COUNT(*) FROM maintainer_email)
  UNION ALL SELECT 'maintained_by',             2, (SELECT COUNT(*) FROM maintained_by)
  UNION ALL SELECT 'depends_on',                1, (SELECT COUNT(*) FROM depends_on)
  UNION ALL SELECT 'resolves_to',               3, (SELECT COUNT(*) FROM resolves_to)
  UNION ALL SELECT 'scan_run',                  2, (SELECT COUNT(*) FROM scan_run)
  UNION ALL SELECT 'observes',                  2, (SELECT COUNT(*) FROM observes)
  UNION ALL SELECT 'vulnerability',             2, (SELECT COUNT(*) FROM vulnerability)
  UNION ALL SELECT 'affects',                   2, (SELECT COUNT(*) FROM affects)
  UNION ALL SELECT 'waived_for',                1, (SELECT COUNT(*) FROM waived_for)
  UNION ALL SELECT 'remediation_plan',          2, (SELECT COUNT(*) FROM remediation_plan)
  UNION ALL SELECT 'remediation_action',        2, (SELECT COUNT(*) FROM remediation_action)
  UNION ALL SELECT 'resolves',                  2, (SELECT COUNT(*) FROM resolves)
  UNION ALL SELECT 'pr_review',                 2, (SELECT COUNT(*) FROM pr_review)
  UNION ALL SELECT 'pr_risk_verdict',           2, (SELECT COUNT(*) FROM pr_risk_verdict)
) AS t;
