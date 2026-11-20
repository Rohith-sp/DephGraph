-- =====================================================================
-- DephGraph — extra sample data for the "complex SQL" queries (MySQL 8.4)
--
-- seed.sql loads only the small Phase 2 example (log4j in payments-svc),
-- which is right for checking Q1-Q7 by hand but too small to make
-- subqueries, outer joins, GROUP BY / HAVING and recursion interesting.
-- This file ADDS rows on top of it:
--
--   * 2 more teams (one owns nothing), 3 more services (one never deployed)
--   * deployments of auth-svc (PROD + DEV), reports-svc, web-frontend
--   * Maven, npm, PyPI and Go packages, with multi-level depends_on chains:
--       spring-boot-starter-web -> spring-boot-starter-json
--                               -> jackson-databind -> jackson-core   (3 levels)
--       express -> body-parser -> qs, and express -> qs directly      (a diamond)
--   * 11 more vulnerabilities (LOW to CRITICAL, one with no CVSS score,
--     one that affects no version we have)
--   * more maintainers (one with no activity date), 2 more clusters,
--     cluster "http client" gets 3 members, 2 more waivers (one expired)
--   * more remediation plans, actions, PRs and verdicts
--
-- Purely additive: new IDs only, no base row is changed or deleted.
-- IDs avoid the ones constraint_demos.sql uses (package 103, vuln 902).
-- All names, versions and scores are realistic but made up.
--
-- Run after seed.sql (seed.sql empties every table, so this can be
-- re-run after re-running seed.sql):
--   scripts/mysql.sh run sql/seed.sql
--   scripts/mysql.sh run sql/seed_extra.sql
-- =====================================================================

USE dephgraph;

START TRANSACTION;

-- ---------------------------------------------------------------------
-- Organisation layer
-- ---------------------------------------------------------------------
INSERT INTO team (team_id, team_name, contact_email, contact_slack) VALUES
  (3, 'Data',   'data@example.com',   '#data'),
  (4, 'Mobile', 'mobile@example.com', NULL);          -- owns no service yet

INSERT INTO service (service_id, service_name, repo_url, criticality, team_id) VALUES
  (12, 'reports-svc',  'https://git.example.com/data/reports-svc',       'MEDIUM', 3),
  (13, 'web-frontend', 'https://git.example.com/platform/web-frontend',  'HIGH',   2),
  (14, 'legacy-batch', 'https://git.example.com/payments/legacy-batch',  'LOW',    1);  -- never deployed

INSERT INTO deployment (service_id, deploy_seq, environment, deployed_at, is_active) VALUES
  (11, 1, 'PROD',    '2026-09-20 08:00:00', TRUE),
  (11, 2, 'DEV',     '2026-11-01 14:00:00', TRUE),
  (12, 1, 'PROD',    '2026-08-15 12:00:00', TRUE),
  (13, 1, 'PROD',    '2026-09-10 09:30:00', TRUE),
  (13, 2, 'STAGING', '2026-06-01 09:30:00', FALSE);   -- old, switched off

-- ---------------------------------------------------------------------
-- Package catalogue
-- ---------------------------------------------------------------------
INSERT INTO license (license_id, spdx_id, license_name, is_copyleft, is_permissive) VALUES
  (3, 'MIT',           'MIT License',                              FALSE, TRUE),
  (4, 'GPL-2.0-only',  'GNU General Public License v2.0 only',     TRUE,  FALSE),
  (5, 'BSD-3-Clause',  'BSD 3-Clause "New" or "Revised" License',  FALSE, TRUE),
  (6, 'MPL-2.0',       'Mozilla Public License 2.0',               TRUE,  FALSE);  -- used by no version

INSERT INTO semantic_cluster (cluster_id, cluster_label, representative_package_id, cohesion_score) VALUES
  (5, 'json parsing', 113, 0.810),
  (6, 'yaml parsing', 115, 0.900);

INSERT INTO package (package_id, package_name, ecosystem, is_internal, source_repo_url, cluster_id) VALUES
  -- Maven (Spring Boot web stack)
  (110, 'spring-boot-starter-web',  'Maven', FALSE, 'https://github.com/spring-projects/spring-boot', NULL),
  (111, 'spring-boot-starter',      'Maven', FALSE, 'https://github.com/spring-projects/spring-boot', NULL),
  (112, 'spring-boot-starter-json', 'Maven', FALSE, 'https://github.com/spring-projects/spring-boot', NULL),
  (113, 'jackson-databind',         'Maven', FALSE, 'https://github.com/FasterXML/jackson-databind', 5),
  (114, 'jackson-core',             'Maven', FALSE, 'https://github.com/FasterXML/jackson-core',     NULL),
  (115, 'snakeyaml',                'Maven', FALSE, 'https://bitbucket.org/snakeyaml/snakeyaml',     6),
  (116, 'json-smart',               'Maven', FALSE, 'https://github.com/netplex/json-smart-v2',      5),
  -- npm
  (120, 'express',     'npm', FALSE, 'https://github.com/expressjs/express',     NULL),
  (121, 'body-parser', 'npm', FALSE, 'https://github.com/expressjs/body-parser', NULL),
  (122, 'qs',          'npm', FALSE, 'https://github.com/ljharb/qs',             NULL),
  (123, 'axios',       'npm', FALSE, 'https://github.com/axios/axios',           4),
  (124, 'node-fetch',  'npm', FALSE, 'https://github.com/node-fetch/node-fetch', 4),
  (125, 'lodash',      'npm', FALSE, 'https://github.com/lodash/lodash',         NULL),
  -- PyPI
  (130, 'requests',               'PyPI', FALSE, 'https://github.com/psf/requests',     4),
  (131, 'urllib3',                'PyPI', FALSE, 'https://github.com/urllib3/urllib3',  NULL),
  (132, 'pyyaml',                 'PyPI', FALSE, 'https://github.com/yaml/pyyaml',      6),
  (133, 'mysql-connector-python', 'PyPI', FALSE, 'https://github.com/mysql/mysql-connector-python', NULL),
  -- Go (in the catalogue, used by no deployment)
  (140, 'golang.org/x/text',      'Go',   FALSE, 'https://github.com/golang/text',      NULL),
  -- internal library of the Payments team
  (150, 'payments-common',        'Maven', TRUE, 'https://git.example.com/payments/payments-common', NULL);

INSERT INTO package_alias (package_id, alias) VALUES
  (113, 'com.fasterxml.jackson.core:jackson-databind'),
  (115, 'org.yaml:snakeyaml'),
  (132, 'PyYAML'),
  (132, 'yaml');

-- payments-common 1.4.0 has no licence recorded (license_id NULL: LICENSED_UNDER is partial)
INSERT INTO package_version (package_id, version_string, published_at, license_id, sha256) VALUES
  (110, '2.6.6',    '2022-03-31', 1, SHA2('spring-boot-starter-web@2.6.6', 256)),
  (111, '2.6.6',    '2022-03-31', 1, SHA2('spring-boot-starter@2.6.6', 256)),
  (112, '2.6.6',    '2022-03-31', 1, SHA2('spring-boot-starter-json@2.6.6', 256)),
  (113, '2.13.2',   '2022-03-06', 1, SHA2('jackson-databind@2.13.2', 256)),
  (113, '2.15.2',   '2023-05-30', 1, SHA2('jackson-databind@2.15.2', 256)),
  (114, '2.13.2',   '2022-03-06', 1, SHA2('jackson-core@2.13.2', 256)),
  (115, '1.29',     '2021-06-08', 1, SHA2('snakeyaml@1.29', 256)),
  (115, '2.0',      '2023-02-26', 1, SHA2('snakeyaml@2.0', 256)),
  (116, '2.4.7',    '2021-05-25', 1, SHA2('json-smart@2.4.7', 256)),
  (120, '4.17.1',   '2019-05-26', 3, SHA2('express@4.17.1', 256)),
  (121, '1.19.0',   '2019-04-26', 3, SHA2('body-parser@1.19.0', 256)),
  (122, '6.7.0',    '2019-03-23', 5, SHA2('qs@6.7.0', 256)),
  (123, '0.21.1',   '2020-12-22', 3, SHA2('axios@0.21.1', 256)),
  (124, '2.6.1',    '2020-09-05', 3, SHA2('node-fetch@2.6.1', 256)),
  (125, '4.17.20',  '2020-08-13', 3, SHA2('lodash@4.17.20', 256)),
  (125, '4.17.21',  '2021-02-20', 3, SHA2('lodash@4.17.21', 256)),
  (130, '2.25.1',   '2020-12-16', 1, SHA2('requests@2.25.1', 256)),
  (131, '1.26.4',   '2021-03-15', 3, SHA2('urllib3@1.26.4', 256)),
  (132, '5.3.1',    '2020-03-18', 3, SHA2('pyyaml@5.3.1', 256)),
  (133, '8.0.33',   '2023-04-18', 4, SHA2('mysql-connector-python@8.0.33', 256)),
  (140, 'v0.3.7',   '2021-08-20', 5, SHA2('golang.org/x/text@v0.3.7', 256)),
  (150, '1.4.0',    '2026-07-01', NULL, SHA2('payments-common@1.4.0', 256));

INSERT INTO maintainer (maintainer_id, handle, display_name, last_active_at) VALUES
  (202, 'cowtowncoder',  'Tatu S.',      '2026-11-10'),
  (203, 'pjfanning',     'P. Fanning',   '2026-10-30'),
  (204, 'asomov',        'A. Somov',     '2024-01-15'),
  (205, 'dougwilson',    'D. Wilson',    '2024-03-01'),
  (206, 'ljharb',        'J. Harband',   '2026-11-05'),
  (207, 'jasonsaayman',  'J. Saayman',   '2026-09-20'),
  (208, 'jdalton',       'J. Dalton',    '2023-06-10'),
  (209, 'sethmlarson',   'S. Larson',    '2026-11-01'),
  (210, 'yaml-keeper',   NULL,           NULL),          -- activity unknown
  (211, 'spring-team',   'Spring Team',  '2026-11-15');

INSERT INTO maintainer_email (maintainer_id, email) VALUES
  (202, 'tatu@example.com'),
  (204, 'asomov@example.com'),
  (205, 'doug@example.com'),
  (205, 'doug@example.org'),
  (209, 'seth@example.com'),
  (211, 'spring@example.com');

INSERT INTO maintained_by (package_id, maintainer_id, role, since_date) VALUES
  (113, 202, 'OWNER',       '2012-07-01'),
  (113, 203, 'COMMITTER',   '2020-01-15'),
  (114, 202, 'OWNER',       '2012-07-01'),
  (116, 202, 'COMMITTER',   '2021-03-01'),
  (115, 204, 'OWNER',       '2009-05-01'),
  (120, 205, 'OWNER',       '2014-06-01'),
  (121, 205, 'OWNER',       '2014-06-01'),
  (122, 206, 'OWNER',       '2015-01-10'),
  (123, 207, 'OWNER',       '2019-08-01'),
  (125, 208, 'OWNER',       '2012-04-23'),
  (130, 209, 'COMMITTER',   '2021-02-01'),
  (131, 209, 'OWNER',       '2019-11-01'),
  (132, 210, 'OWNER',       '2018-01-01'),
  (110, 211, 'OWNER',       '2014-04-01'),
  (111, 211, 'OWNER',       '2014-04-01'),
  (112, 211, 'OWNER',       '2014-04-01');

-- ---------------------------------------------------------------------
-- Dependency graph
-- ---------------------------------------------------------------------
INSERT INTO depends_on (dependent_package_id, dependent_version,
                        dependency_package_id, dependency_version, scope, is_direct) VALUES
  -- Spring Boot web stack: 3 levels below spring-boot-starter-web
  (110, '2.6.6',  111, '2.6.6',  'runtime', TRUE),
  (110, '2.6.6',  112, '2.6.6',  'runtime', TRUE),
  (111, '2.6.6',  115, '1.29',   'runtime', TRUE),
  (112, '2.6.6',  113, '2.13.2', 'runtime', TRUE),
  (113, '2.13.2', 114, '2.13.2', 'runtime', TRUE),
  -- express: a diamond, qs is reached directly and through body-parser
  (120, '4.17.1', 121, '1.19.0', 'runtime', TRUE),
  (120, '4.17.1', 122, '6.7.0',  'runtime', TRUE),
  (121, '1.19.0', 122, '6.7.0',  'runtime', TRUE),
  -- requests -> urllib3
  (130, '2.25.1', 131, '1.26.4', 'runtime', TRUE),
  -- the internal library uses the fixed log4j-core
  (150, '1.4.0',  100, '2.17.1', 'runtime', TRUE);

-- depth = shortest number of depends_on edges from a direct dependency (0 = direct)
INSERT INTO resolves_to (service_id, deploy_seq, package_id, version_string, depth, resolved_at) VALUES
  -- payments-svc STAGING (base deployment 10/2, had no resolved packages yet)
  (10, 2, 150, '1.4.0',   0, '2026-10-05 16:30:00'),
  (10, 2, 100, '2.17.1',  1, '2026-10-05 16:30:00'),
  -- auth-svc PROD
  (11, 1, 110, '2.6.6',   0, '2026-09-20 08:00:00'),
  (11, 1, 111, '2.6.6',   1, '2026-09-20 08:00:00'),
  (11, 1, 112, '2.6.6',   1, '2026-09-20 08:00:00'),
  (11, 1, 115, '1.29',    2, '2026-09-20 08:00:00'),
  (11, 1, 113, '2.13.2',  2, '2026-09-20 08:00:00'),
  (11, 1, 114, '2.13.2',  3, '2026-09-20 08:00:00'),
  (11, 1, 100, '2.17.1',  0, '2026-09-20 08:00:00'),
  -- auth-svc DEV: someone is trying the old log4j-core
  (11, 2, 100, '2.14.1',  0, '2026-11-01 14:00:00'),
  -- reports-svc PROD
  (12, 1, 130, '2.25.1',  0, '2026-08-15 12:00:00'),
  (12, 1, 131, '1.26.4',  1, '2026-08-15 12:00:00'),
  (12, 1, 132, '5.3.1',   0, '2026-08-15 12:00:00'),
  (12, 1, 133, '8.0.33',  0, '2026-08-15 12:00:00'),
  -- web-frontend PROD
  (13, 1, 120, '4.17.1',  0, '2026-09-10 09:30:00'),
  (13, 1, 121, '1.19.0',  1, '2026-09-10 09:30:00'),
  (13, 1, 122, '6.7.0',   1, '2026-09-10 09:30:00'),
  (13, 1, 123, '0.21.1',  0, '2026-09-10 09:30:00'),
  (13, 1, 124, '2.6.1',   0, '2026-09-10 09:30:00'),
  (13, 1, 125, '4.17.20', 0, '2026-09-10 09:30:00'),
  -- web-frontend old STAGING (inactive)
  (13, 2, 125, '4.17.21', 0, '2026-06-01 09:30:00');

-- ---------------------------------------------------------------------
-- Runtime evidence
-- ---------------------------------------------------------------------
INSERT INTO scan_run (scan_id, source_tool, started_at, ended_at) VALUES
  (502, 'ebpf-agent',      '2026-10-01 00:00:00', '2026-10-31 00:00:00'),
  (503, 'manifest-parser', '2026-11-20 03:00:00', NULL);   -- still running

INSERT INTO observes (service_id, deploy_seq, package_id, version_string, scan_id,
                      exec_count, first_seen, last_seen) VALUES
  (10, 1, 100, '2.14.1',  502, 2100,    '2026-10-01 00:02:00', '2026-10-30 22:10:00'),
  (10, 1, 101, '1.2.3',   502, 0,       NULL, NULL),
  (11, 1, 110, '2.6.6',   502, 0,       NULL, NULL),
  (11, 1, 115, '1.29',    502, 12000,   '2026-10-01 00:01:00', '2026-10-30 23:59:00'),
  (11, 1, 113, '2.13.2',  502, 90500,   '2026-10-01 00:01:00', '2026-10-30 23:59:00'),
  (11, 1, 114, '2.13.2',  502, 90500,   '2026-10-01 00:01:00', '2026-10-30 23:59:00'),
  (11, 1, 100, '2.17.1',  502, 5000,    '2026-10-01 00:01:00', '2026-10-30 23:58:00'),
  (12, 1, 130, '2.25.1',  502, 4000,    '2026-10-01 01:00:00', '2026-10-30 20:00:00'),
  (12, 1, 131, '1.26.4',  502, 4000,    '2026-10-01 01:00:00', '2026-10-30 20:00:00'),
  (12, 1, 132, '5.3.1',   502, 0,       NULL, NULL),
  (12, 1, 133, '8.0.33',  502, 2000,    '2026-10-01 01:00:00', '2026-10-30 20:00:00'),
  (13, 1, 120, '4.17.1',  502, 1000000, '2026-10-01 00:00:10', '2026-10-30 23:59:59'),
  (13, 1, 121, '1.19.0',  502, 990000,  '2026-10-01 00:00:10', '2026-10-30 23:59:59'),
  (13, 1, 122, '6.7.0',   502, 990000,  '2026-10-01 00:00:10', '2026-10-30 23:59:59'),
  (13, 1, 123, '0.21.1',  502, 300,     '2026-10-02 10:00:00', '2026-10-29 18:00:00'),
  (13, 1, 124, '2.6.1',   502, 0,       NULL, NULL),
  (13, 1, 125, '4.17.20', 502, 0,       NULL, NULL);

-- ---------------------------------------------------------------------
-- Vulnerabilities
-- ---------------------------------------------------------------------
INSERT INTO vulnerability (vuln_id, osv_id, summary, severity, cvss_score, published_at) VALUES
  (910, 'GHSA-mjmj-j48q-9wg2', 'SnakeYAML Constructor deserialization allows remote code execution', 'HIGH',     8.3, '2022-12-01'),
  (911, 'GHSA-jjjh-jjxp-wpff', 'Uncontrolled resource consumption in jackson-databind',               'HIGH',     7.5, '2022-10-02'),
  (912, 'GHSA-hrpp-h998-j3pp', 'qs prototype pollution',                                              'HIGH',     7.5, '2022-11-26'),
  (913, 'GHSA-cph5-m8f7-6c5x', 'Inefficient regular expression in axios',                             'HIGH',     7.5, '2021-08-31'),
  (914, 'GHSA-r683-j2x4-v87g', 'node-fetch forwards secure headers to untrusted sites',               'MEDIUM',   6.5, '2022-01-16'),
  (915, 'GHSA-35jh-r3h4-6jhm', 'Command injection in lodash template',                                'HIGH',     7.2, '2021-02-15'),
  (916, 'GHSA-29mw-wpgm-hmr9', 'Regular expression denial of service in lodash',                      'MEDIUM',   5.3, '2021-02-15'),
  (917, 'GHSA-8q59-q68h-6hv4', 'Arbitrary code execution in PyYAML full_load',                        'CRITICAL', 9.8, '2021-03-24'),
  (918, 'GHSA-v845-jxx5-vc9f', 'urllib3 does not strip Cookie header on cross-origin redirect',       'MEDIUM',   5.9, '2023-10-02'),
  (919, 'GHSA-g4mx-q9vg-27p4', 'urllib3 request body not stripped after 303 redirect',                'LOW',      3.1, '2023-10-17'),
  (920, 'DG-2026-0001',        'Internal advisory: payments-common logs card BIN in debug mode',      'LOW',      NULL, '2026-11-12'),
  (921, 'GHSA-p6xc-xr62-6r2g', 'log4j-core 2.16.0 infinite recursion in lookup evaluation',           'HIGH',     5.9, '2021-12-18');
  -- 921 affects only log4j-core 2.16.0, which no deployment uses: it has no AFFECTS row here

INSERT INTO affects (vuln_id, package_id, version_string, fixed_in) VALUES
  (910, 115, '1.29',    '2.0'),
  (911, 113, '2.13.2',  '2.13.4'),
  (912, 122, '6.7.0',   '6.7.3'),
  (913, 123, '0.21.1',  '0.21.2'),
  (914, 124, '2.6.1',   '2.6.7'),
  (915, 125, '4.17.20', '4.17.21'),
  (916, 125, '4.17.20', '4.17.21'),
  (917, 132, '5.3.1',   '5.4'),
  (918, 131, '1.26.4',  '1.26.17'),
  (919, 131, '1.26.4',  '1.26.18'),
  (920, 150, '1.4.0',   NULL);                       -- no fix released yet

INSERT INTO waived_for (vuln_id, service_id, reason, approved_by, expires_on) VALUES
  (913, 13, 'axios only calls internal APIs with fixed URLs; upgrade in plan 52',
        'security-lead', '2026-12-31'),
  (912, 13, 'qs parsing limited by API gateway; waiver not renewed',
        'security-lead', '2026-10-31');               -- already expired

-- ---------------------------------------------------------------------
-- Remediation
-- ---------------------------------------------------------------------
INSERT INTO remediation_plan (plan_id, team_id, created_at, status) VALUES
  (52, 2, '2026-11-03 10:00:00', 'APPROVED'),
  (53, 3, '2026-10-20 09:00:00', 'DONE'),
  (54, 1, '2026-11-12 15:00:00', 'DRAFT');

INSERT INTO remediation_action (plan_id, action_seq, action_type, target_version, est_effort_hrs) VALUES
  (52, 1, 'UPGRADE',     '4.17.21', 2.0),
  (52, 2, 'UPGRADE',     '0.21.2',  1.0),
  (52, 3, 'CONSOLIDATE', NULL,      6.0),          -- axios + node-fetch -> one http client
  (53, 1, 'UPGRADE',     '5.4',     3.0),
  (54, 1, 'UPGRADE',     '1.4.1',   NULL);         -- effort not estimated yet

INSERT INTO resolves (plan_id, action_seq, vuln_id) VALUES
  (52, 1, 915),
  (52, 1, 916),
  (52, 2, 913),
  (53, 1, 917),
  (54, 1, 920);

-- ---------------------------------------------------------------------
-- PR risk gate
-- ---------------------------------------------------------------------
INSERT INTO pr_review (service_id, pr_number, title, author, opened_at, merged_at, filter_stage_exit) VALUES
  (13, 101, 'Add node-fetch for server-side rendering', 'carol', '2026-10-25 15:00:00', NULL,                  'STAGE_3'),
  (12, 5,   'Upgrade PyYAML',                           'dave',  '2026-10-28 10:00:00', '2026-10-29 09:00:00', 'STAGE_1'),
  (11, 8,   'Add YAML config support',                  'bob',   '2026-11-02 13:20:00', NULL,                  'STAGE_2');

INSERT INTO pr_risk_verdict (service_id, pr_number, dep_name, dep_ecosystem, dep_version, verdict, reason,
                             cve_count_seen, abandonment_seen, siblings_seen, context_tokens, model_used,
                             decided_at) VALUES
  (13, 101, 'node-fetch', 'npm',   '2.6.1', 'RISK',    'header leak on redirect; axios already does this job',
        1, 20.0, 2, 3400, 'llama-3.3-70b', '2026-10-25 15:02:00'),
  (13, 101, 'left-pad',   'npm',   '1.3.0', 'WARN',    'single maintainer, no release in years',
        0, 88.0, 0, 2100, 'llama-3.3-70b', '2026-10-25 15:02:00'),
  (12, 5,   'pyyaml',     'PyPI',  '5.4.1', 'APPROVE', 'fixes GHSA-8q59-q68h-6hv4',
        0, 45.0, 1, NULL, NULL,            '2026-10-28 10:01:00'),
  (11, 8,   'snakeyaml',  'Maven', '1.29',  'WARN',    'known HIGH vulnerability, fix in 2.0',
        1, 75.0, 1, NULL, NULL,            '2026-11-02 13:21:00');

COMMIT;

-- ---------------------------------------------------------------------
-- Check: row count per table after seed.sql + seed_extra.sql
-- ---------------------------------------------------------------------
SELECT t.tbl AS table_name, t.expected, t.found,
       IF(t.expected = t.found, 'ok', 'MISMATCH') AS result
FROM (
            SELECT 'team'               AS tbl,  4 AS expected, (SELECT COUNT(*) FROM team)  AS found
  UNION ALL SELECT 'service',                    5, (SELECT COUNT(*) FROM service)
  UNION ALL SELECT 'deployment',                 7, (SELECT COUNT(*) FROM deployment)
  UNION ALL SELECT 'license',                    6, (SELECT COUNT(*) FROM license)
  UNION ALL SELECT 'semantic_cluster',           4, (SELECT COUNT(*) FROM semantic_cluster)
  UNION ALL SELECT 'package',                   22, (SELECT COUNT(*) FROM package)
  UNION ALL SELECT 'package_alias',              6, (SELECT COUNT(*) FROM package_alias)
  UNION ALL SELECT 'package_version',           26, (SELECT COUNT(*) FROM package_version)
  UNION ALL SELECT 'maintainer',                12, (SELECT COUNT(*) FROM maintainer)
  UNION ALL SELECT 'maintainer_email',           9, (SELECT COUNT(*) FROM maintainer_email)
  UNION ALL SELECT 'maintained_by',             18, (SELECT COUNT(*) FROM maintained_by)
  UNION ALL SELECT 'depends_on',                11, (SELECT COUNT(*) FROM depends_on)
  UNION ALL SELECT 'resolves_to',               24, (SELECT COUNT(*) FROM resolves_to)
  UNION ALL SELECT 'scan_run',                   4, (SELECT COUNT(*) FROM scan_run)
  UNION ALL SELECT 'observes',                  19, (SELECT COUNT(*) FROM observes)
  UNION ALL SELECT 'vulnerability',             14, (SELECT COUNT(*) FROM vulnerability)
  UNION ALL SELECT 'affects',                   13, (SELECT COUNT(*) FROM affects)
  UNION ALL SELECT 'waived_for',                 3, (SELECT COUNT(*) FROM waived_for)
  UNION ALL SELECT 'remediation_plan',           5, (SELECT COUNT(*) FROM remediation_plan)
  UNION ALL SELECT 'remediation_action',         7, (SELECT COUNT(*) FROM remediation_action)
  UNION ALL SELECT 'resolves',                   7, (SELECT COUNT(*) FROM resolves)
  UNION ALL SELECT 'pr_review',                  5, (SELECT COUNT(*) FROM pr_review)
  UNION ALL SELECT 'pr_risk_verdict',            6, (SELECT COUNT(*) FROM pr_risk_verdict)
) AS t;
