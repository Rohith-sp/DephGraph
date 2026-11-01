-- =====================================================================
-- DephGraph — Dependency Intelligence Platform
-- Physical schema (MySQL 8.0+)
--
-- HOW TO USE IN MySQL WORKBENCH
--   1. Server > Data Import, or simply File > Open SQL Script > Run (Ctrl+Shift+Enter)
--   2. Database > Reverse Engineer  (Ctrl+R)
--   3. Pick schema `dephgraph` > Execute > Next > Finish
--      -> Workbench auto-generates the EER diagram with all FK connectors.
--
-- NOTE ON NOTATION
--   Workbench draws crow's-foot EER diagrams, not Chen notation.
--   The Chen ER diagram (ellipses for attributes, diamonds for
--   relationships) is supplied separately as dephgraph_er_chen.drawio.
-- =====================================================================

DROP DATABASE IF EXISTS dephgraph;
CREATE DATABASE dephgraph CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE dephgraph;

-- =====================================================================
-- SECTION 1 — ORGANISATION LAYER
-- =====================================================================

CREATE TABLE team (
    team_id        INT AUTO_INCREMENT PRIMARY KEY,
    team_name      VARCHAR(100) NOT NULL UNIQUE,
    -- composite attribute `contact` decomposed into its parts
    contact_email  VARCHAR(150) NOT NULL,
    contact_slack  VARCHAR(80),
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE service (
    service_id     INT AUTO_INCREMENT PRIMARY KEY,
    service_name   VARCHAR(120) NOT NULL UNIQUE,
    repo_url       VARCHAR(255),
    criticality    ENUM('LOW','MEDIUM','HIGH','CRITICAL') NOT NULL DEFAULT 'MEDIUM',
    team_id        INT NOT NULL,          -- total participation in OWNS
    CONSTRAINT fk_service_team FOREIGN KEY (team_id)
        REFERENCES team(team_id) ON DELETE RESTRICT
) ENGINE=InnoDB;

CREATE INDEX idx_service_team ON service(team_id);

-- WEAK ENTITY: a deployment cannot exist without its service.
-- Partial key = deploy_seq, unique only within the owning service.
CREATE TABLE deployment (
    service_id     INT NOT NULL,
    deploy_seq     INT NOT NULL,                       -- partial key
    environment    ENUM('DEV','STAGING','PROD') NOT NULL,
    deployed_at    TIMESTAMP NOT NULL,
    is_active      BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (service_id, deploy_seq),               -- composite PK
    CONSTRAINT fk_deployment_service FOREIGN KEY (service_id)
        REFERENCES service(service_id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE INDEX idx_deployment_env_active ON deployment(environment, is_active);

-- =====================================================================
-- SECTION 2 — PACKAGE LAYER
-- =====================================================================

CREATE TABLE license (
    license_id     INT AUTO_INCREMENT PRIMARY KEY,
    spdx_id        VARCHAR(60) NOT NULL UNIQUE,         -- MIT, GPL-3.0-only
    license_name   VARCHAR(150),
    is_copyleft    BOOLEAN NOT NULL DEFAULT FALSE,
    is_permissive  BOOLEAN NOT NULL DEFAULT TRUE
) ENGINE=InnoDB;

CREATE TABLE package (
    package_id       INT AUTO_INCREMENT PRIMARY KEY,
    package_name     VARCHAR(200) NOT NULL,
    ecosystem        ENUM('npm','PyPI','Maven','Go','crates.io','NuGet') NOT NULL,
    is_internal      BOOLEAN NOT NULL DEFAULT FALSE,
    source_repo_url  VARCHAR(255),
    cluster_id       INT NULL,                          -- partial participation
    UNIQUE KEY uq_package (package_name, ecosystem)
) ENGINE=InnoDB;

-- MULTIVALUED attribute `alias` -> its own table (1:N from package)
CREATE TABLE package_alias (
    package_id     INT NOT NULL,
    alias          VARCHAR(200) NOT NULL,
    PRIMARY KEY (package_id, alias),
    CONSTRAINT fk_alias_package FOREIGN KEY (package_id)
        REFERENCES package(package_id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- WEAK ENTITY: a version is meaningless without its package.
CREATE TABLE package_version (
    package_id     INT NOT NULL,
    version_string VARCHAR(60) NOT NULL,                -- partial key
    published_at   DATE,
    license_id     INT,
    sha256         CHAR(64),
    PRIMARY KEY (package_id, version_string),
    CONSTRAINT fk_pv_package FOREIGN KEY (package_id)
        REFERENCES package(package_id) ON DELETE CASCADE,
    CONSTRAINT fk_pv_license FOREIGN KEY (license_id)
        REFERENCES license(license_id) ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE INDEX idx_pv_license ON package_version(license_id);

CREATE TABLE maintainer (
    maintainer_id   INT AUTO_INCREMENT PRIMARY KEY,
    handle          VARCHAR(100) NOT NULL UNIQUE,
    display_name    VARCHAR(150),
    last_active_at  DATE
) ENGINE=InnoDB;

-- MULTIVALUED attribute `email`
CREATE TABLE maintainer_email (
    maintainer_id  INT NOT NULL,
    email          VARCHAR(150) NOT NULL,
    PRIMARY KEY (maintainer_id, email),
    CONSTRAINT fk_memail_maintainer FOREIGN KEY (maintainer_id)
        REFERENCES maintainer(maintainer_id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- M:N  MAINTAINED_BY
CREATE TABLE maintained_by (
    package_id     INT NOT NULL,
    maintainer_id  INT NOT NULL,
    role           ENUM('OWNER','COMMITTER','CONTRIBUTOR') NOT NULL DEFAULT 'COMMITTER',
    since_date     DATE,
    PRIMARY KEY (package_id, maintainer_id),
    CONSTRAINT fk_mb_package FOREIGN KEY (package_id)
        REFERENCES package(package_id) ON DELETE CASCADE,
    CONSTRAINT fk_mb_maintainer FOREIGN KEY (maintainer_id)
        REFERENCES maintainer(maintainer_id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- =====================================================================
-- SECTION 3 — THE DEPENDENCY GRAPH  (recursive M:N)
-- =====================================================================
-- DEPENDS_ON is a recursive relationship on PACKAGE_VERSION.
-- Roles: the "dependent" version requires the "dependency" version.

CREATE TABLE depends_on (
    dependent_package_id   INT NOT NULL,
    dependent_version      VARCHAR(60) NOT NULL,
    dependency_package_id  INT NOT NULL,
    dependency_version     VARCHAR(60) NOT NULL,
    scope                  ENUM('runtime','dev','optional','peer') NOT NULL DEFAULT 'runtime',
    is_direct              BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (dependent_package_id, dependent_version,
                 dependency_package_id, dependency_version, scope),
    CONSTRAINT fk_dep_from FOREIGN KEY (dependent_package_id, dependent_version)
        REFERENCES package_version(package_id, version_string) ON DELETE CASCADE,
    CONSTRAINT fk_dep_to   FOREIGN KEY (dependency_package_id, dependency_version)
        REFERENCES package_version(package_id, version_string) ON DELETE CASCADE,
    CONSTRAINT chk_no_self_dep CHECK (
        NOT (dependent_package_id = dependency_package_id
             AND dependent_version = dependency_version)
    )
) ENGINE=InnoDB;

-- Reverse index: "who depends on me" is the hot query for impact analysis
CREATE INDEX idx_depends_reverse
    ON depends_on(dependency_package_id, dependency_version);

-- M:N  RESOLVES_TO — what a deployment's lockfile actually resolved to
CREATE TABLE resolves_to (
    service_id     INT NOT NULL,
    deploy_seq     INT NOT NULL,
    package_id     INT NOT NULL,
    version_string VARCHAR(60) NOT NULL,
    depth          SMALLINT NOT NULL DEFAULT 0,          -- 0 = direct
    resolved_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (service_id, deploy_seq, package_id, version_string),
    CONSTRAINT fk_rt_deployment FOREIGN KEY (service_id, deploy_seq)
        REFERENCES deployment(service_id, deploy_seq) ON DELETE CASCADE,
    CONSTRAINT fk_rt_version FOREIGN KEY (package_id, version_string)
        REFERENCES package_version(package_id, version_string) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE INDEX idx_rt_version ON resolves_to(package_id, version_string);

-- =====================================================================
-- SECTION 4 — RUNTIME EVIDENCE  (ternary relationship)
-- =====================================================================
-- OBSERVES binds DEPLOYMENT x PACKAGE_VERSION x SCAN_RUN.
-- This is the eBPF liveness evidence: did this package's code ever
-- execute in this deployment, during this observation window?
-- It CANNOT be decomposed into binary relationships without losing
-- which observation window produced the evidence.

CREATE TABLE scan_run (
    scan_id        INT AUTO_INCREMENT PRIMARY KEY,
    source_tool    ENUM('ebpf-agent','manifest-parser','osv-sync','deps-dev-sync') NOT NULL,
    started_at     TIMESTAMP NOT NULL,
    ended_at       TIMESTAMP NULL,
    CONSTRAINT chk_scan_window CHECK (ended_at IS NULL OR ended_at >= started_at)
) ENGINE=InnoDB;

CREATE TABLE observes (
    service_id     INT NOT NULL,
    deploy_seq     INT NOT NULL,
    package_id     INT NOT NULL,
    version_string VARCHAR(60) NOT NULL,
    scan_id        INT NOT NULL,
    exec_count     BIGINT NOT NULL DEFAULT 0,
    first_seen     TIMESTAMP NULL,
    last_seen      TIMESTAMP NULL,
    -- GENERATED (derived) attribute: liveness verdict
    is_live        BOOLEAN AS (exec_count > 0) STORED,
    PRIMARY KEY (service_id, deploy_seq, package_id, version_string, scan_id),
    CONSTRAINT fk_obs_deployment FOREIGN KEY (service_id, deploy_seq)
        REFERENCES deployment(service_id, deploy_seq) ON DELETE CASCADE,
    CONSTRAINT fk_obs_version FOREIGN KEY (package_id, version_string)
        REFERENCES package_version(package_id, version_string) ON DELETE CASCADE,
    CONSTRAINT fk_obs_scan FOREIGN KEY (scan_id)
        REFERENCES scan_run(scan_id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE INDEX idx_observes_live ON observes(is_live, package_id, version_string);

-- =====================================================================
-- SECTION 5 — SEMANTIC CLUSTERING  (ReqCluster transplant)
-- =====================================================================

CREATE TABLE semantic_cluster (
    cluster_id      INT AUTO_INCREMENT PRIMARY KEY,
    cluster_label   VARCHAR(150) NOT NULL,   -- c-TF-IDF label e.g. "http client"
    representative_package_id INT NULL,
    cohesion_score  DECIMAL(4,3),
    CONSTRAINT chk_cohesion CHECK (cohesion_score BETWEEN 0 AND 1)
) ENGINE=InnoDB;

ALTER TABLE package
    ADD CONSTRAINT fk_package_cluster FOREIGN KEY (cluster_id)
        REFERENCES semantic_cluster(cluster_id) ON DELETE SET NULL;

CREATE INDEX idx_package_cluster ON package(cluster_id);

-- =====================================================================
-- SECTION 6 — VULNERABILITY LAYER
-- =====================================================================

CREATE TABLE vulnerability (
    vuln_id        INT AUTO_INCREMENT PRIMARY KEY,
    osv_id         VARCHAR(60) NOT NULL UNIQUE,   -- GHSA-xxxx / CVE-2021-44228
    summary        VARCHAR(500),
    severity       ENUM('LOW','MEDIUM','HIGH','CRITICAL') NOT NULL,
    cvss_score     DECIMAL(3,1),
    published_at   DATE NOT NULL,
    CONSTRAINT chk_cvss CHECK (cvss_score BETWEEN 0 AND 10)
) ENGINE=InnoDB;

-- M:N  AFFECTS
CREATE TABLE affects (
    vuln_id        INT NOT NULL,
    package_id     INT NOT NULL,
    version_string VARCHAR(60) NOT NULL,
    fixed_in       VARCHAR(60),
    PRIMARY KEY (vuln_id, package_id, version_string),
    CONSTRAINT fk_aff_vuln FOREIGN KEY (vuln_id)
        REFERENCES vulnerability(vuln_id) ON DELETE CASCADE,
    CONSTRAINT fk_aff_version FOREIGN KEY (package_id, version_string)
        REFERENCES package_version(package_id, version_string) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE INDEX idx_affects_version ON affects(package_id, version_string);

-- M:N  WAIVED_FOR (vulnerability accepted as risk for a given service)
CREATE TABLE waived_for (
    vuln_id        INT NOT NULL,
    service_id     INT NOT NULL,
    reason         VARCHAR(300) NOT NULL,
    approved_by    VARCHAR(100) NOT NULL,
    expires_on     DATE NOT NULL,
    PRIMARY KEY (vuln_id, service_id),
    CONSTRAINT fk_waiver_vuln FOREIGN KEY (vuln_id)
        REFERENCES vulnerability(vuln_id) ON DELETE CASCADE,
    CONSTRAINT fk_waiver_service FOREIGN KEY (service_id)
        REFERENCES service(service_id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- =====================================================================
-- SECTION 7 — REMEDIATION LAYER
-- =====================================================================

CREATE TABLE remediation_plan (
    plan_id        INT AUTO_INCREMENT PRIMARY KEY,
    service_id     INT NOT NULL UNIQUE,     -- UNIQUE enforces the 1:1
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status         ENUM('DRAFT','APPROVED','IN_PROGRESS','DONE') NOT NULL DEFAULT 'DRAFT',
    CONSTRAINT fk_plan_service FOREIGN KEY (service_id)
        REFERENCES service(service_id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- WEAK ENTITY: an action has no meaning outside its plan
CREATE TABLE remediation_action (
    plan_id        INT NOT NULL,
    action_seq     INT NOT NULL,                        -- partial key
    action_type    ENUM('UPGRADE','REMOVE','CONSOLIDATE','REPLACE') NOT NULL,
    package_id     INT NOT NULL,
    target_version VARCHAR(60),
    est_effort_hrs DECIMAL(5,1),
    PRIMARY KEY (plan_id, action_seq),
    CONSTRAINT fk_action_plan FOREIGN KEY (plan_id)
        REFERENCES remediation_plan(plan_id) ON DELETE CASCADE,
    CONSTRAINT fk_action_package FOREIGN KEY (package_id)
        REFERENCES package(package_id) ON DELETE RESTRICT
) ENGINE=InnoDB;

-- M:N  RESOLVES — one action can clear many vulnerabilities
CREATE TABLE resolves (
    plan_id        INT NOT NULL,
    action_seq     INT NOT NULL,
    vuln_id        INT NOT NULL,
    PRIMARY KEY (plan_id, action_seq, vuln_id),
    CONSTRAINT fk_res_action FOREIGN KEY (plan_id, action_seq)
        REFERENCES remediation_action(plan_id, action_seq) ON DELETE CASCADE,
    CONSTRAINT fk_res_vuln FOREIGN KEY (vuln_id)
        REFERENCES vulnerability(vuln_id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- =====================================================================
-- SECTION 7b — PULL REQUEST RISK GATE  (Feature 8)
-- =====================================================================

-- WEAK ENTITY: a PR number is unique only within its repository, so a PR
-- has no identity outside the service it targets.
-- Identifying relationship: TARGETS (double diamond in the ER diagram).
CREATE TABLE pr_review (
    service_id        INT NOT NULL,
    pr_number         INT NOT NULL,                     -- partial key
    title             VARCHAR(300),
    author            VARCHAR(100),
    opened_at         TIMESTAMP NOT NULL,
    merged_at         TIMESTAMP NULL,
    filter_stage_exit ENUM('STAGE_1','STAGE_2','STAGE_3') NOT NULL,
    PRIMARY KEY (service_id, pr_number),
    CONSTRAINT fk_pr_service FOREIGN KEY (service_id)
        REFERENCES service(service_id) ON DELETE CASCADE,
    CONSTRAINT chk_pr_dates CHECK (merged_at IS NULL OR merged_at >= opened_at)
) ENGINE=InnoDB;

CREATE INDEX idx_pr_stage ON pr_review(filter_stage_exit, opened_at);

-- WEAK ENTITY: one verdict per dependency introduced by the PR.
-- Collapsing verdicts into pr_review would need repeating groups -> 1NF violation.
-- Identifying relationship: PRODUCED_FOR (double diamond).
--
-- NOTE: dep_name deliberately has NO foreign key to package. A pull request
-- may introduce a package that is not yet in the graph; requiring the FK
-- would make the gate unable to evaluate exactly the novel dependencies it
-- most needs to judge. Linkage is resolved after the next ingest run.
CREATE TABLE pr_risk_verdict (
    service_id        INT NOT NULL,
    pr_number         INT NOT NULL,
    dep_name          VARCHAR(200) NOT NULL,            -- partial key
    dep_ecosystem     ENUM('npm','PyPI','Maven','Go','crates.io','NuGet') NOT NULL,
    dep_version       VARCHAR(60) NOT NULL,
    verdict           ENUM('RISK','WARN','APPROVE') NOT NULL,
    reason            VARCHAR(400),
    cve_count_seen    INT      NOT NULL DEFAULT 0,
    abandonment_seen  DECIMAL(4,1),                     -- 0-100, higher = worse
    siblings_seen     INT      NOT NULL DEFAULT 0,
    context_tokens    INT,
    model_used        VARCHAR(60),                      -- NULL for STAGE_1 / STAGE_2
    decided_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (service_id, pr_number, dep_name),
    CONSTRAINT fk_verdict_pr FOREIGN KEY (service_id, pr_number)
        REFERENCES pr_review(service_id, pr_number) ON DELETE CASCADE,
    CONSTRAINT chk_abandon_seen CHECK (abandonment_seen BETWEEN 0 AND 100),
    CONSTRAINT chk_cve_seen     CHECK (cve_count_seen >= 0)
) ENGINE=InnoDB;

CREATE INDEX idx_verdict_dep     ON pr_risk_verdict(dep_name, dep_ecosystem, verdict);
CREATE INDEX idx_verdict_outcome ON pr_risk_verdict(verdict, decided_at);

-- =====================================================================
-- SECTION 8 — DERIVED ATTRIBUTES AS VIEWS
-- (ER-diagram dashed ellipses are computed, never stored)
-- =====================================================================

-- PACKAGE.abandonment_score  — the xz-utils early-warning signal
CREATE OR REPLACE VIEW v_package_abandonment AS
SELECT  p.package_id,
        p.package_name,
        COUNT(DISTINCT mb.maintainer_id)                       AS maintainer_count,
        MAX(m.last_active_at)                                  AS last_activity,
        DATEDIFF(CURDATE(), MAX(m.last_active_at))             AS days_idle,
        -- SCALE IS FIXED SYSTEM-WIDE: 0-100, HIGHER = MORE ABANDONED = WORSE
        ROUND(
            LEAST(100.0,
                  (CASE WHEN COUNT(DISTINCT mb.maintainer_id) <= 1 THEN 50.0 ELSE 0.0 END) +
                  (LEAST(DATEDIFF(CURDATE(), MAX(m.last_active_at)), 730) / 14.6)
            ), 1)                                              AS abandonment_score
FROM        package        p
LEFT JOIN   maintained_by  mb ON mb.package_id = p.package_id
LEFT JOIN   maintainer     m  ON m.maintainer_id = mb.maintainer_id
GROUP BY    p.package_id, p.package_name;

-- SERVICE.exposure_score — live, unwaived, production exposure only
CREATE OR REPLACE VIEW v_service_exposure AS
SELECT  s.service_id,
        s.service_name,
        COUNT(DISTINCT v.vuln_id)                              AS open_vulns,
        SUM(CASE WHEN v.severity = 'CRITICAL' THEN 1 ELSE 0 END) AS critical_vulns,
        ROUND(AVG(v.cvss_score), 2)                            AS avg_cvss
FROM        service         s
JOIN        deployment      d  ON d.service_id = s.service_id
                              AND d.environment = 'PROD' AND d.is_active = TRUE
JOIN        resolves_to     rt ON rt.service_id = d.service_id
                              AND rt.deploy_seq = d.deploy_seq
JOIN        affects         a  ON a.package_id = rt.package_id
                              AND a.version_string = rt.version_string
JOIN        vulnerability   v  ON v.vuln_id = a.vuln_id
LEFT JOIN   waived_for      w  ON w.vuln_id = v.vuln_id
                              AND w.service_id = s.service_id
                              AND w.expires_on >= CURDATE()
WHERE       w.vuln_id IS NULL
GROUP BY    s.service_id, s.service_name;

-- SEMANTIC_CLUSTER.member_count + redundancy detection
CREATE OR REPLACE VIEW v_cluster_redundancy AS
SELECT  sc.cluster_id,
        sc.cluster_label,
        COUNT(p.package_id)                                    AS member_count,
        GROUP_CONCAT(p.package_name ORDER BY p.package_name SEPARATOR ', ') AS packages
FROM        semantic_cluster sc
JOIN        package          p ON p.cluster_id = sc.cluster_id
GROUP BY    sc.cluster_id, sc.cluster_label
HAVING      COUNT(p.package_id) > 1;

-- =====================================================================
-- SECTION 9 — SIGNATURE QUERIES
-- =====================================================================

-- Q1  BLAST RADIUS — recursive transitive closure.
--     "Which services reach log4j-core, and by what path?"
--     Replace the seed values to target any package.
/*
WITH RECURSIVE reachable (package_id, version_string, depth, path) AS (
    -- anchor: the vulnerable package version
    SELECT  pv.package_id, pv.version_string, 0,
            CAST(CONCAT(p.package_name, '@', pv.version_string) AS CHAR(2000))
    FROM    package_version pv
    JOIN    package p ON p.package_id = pv.package_id
    WHERE   p.package_name = 'log4j-core'

    UNION ALL

    -- walk UPWARD: who depends on what we already reached
    SELECT  d.dependent_package_id, d.dependent_version, r.depth + 1,
            CONCAT(p2.package_name, '@', d.dependent_version, ' -> ', r.path)
    FROM    reachable  r
    JOIN    depends_on d  ON d.dependency_package_id = r.package_id
                         AND d.dependency_version    = r.version_string
    JOIN    package    p2 ON p2.package_id = d.dependent_package_id
    WHERE   r.depth < 15                    -- cycle guard
)
SELECT   s.service_name, d.environment, MIN(r.depth) AS hops, MIN(r.path) AS dependency_path
FROM     reachable   r
JOIN     resolves_to rt ON rt.package_id = r.package_id
                       AND rt.version_string = r.version_string
JOIN     deployment  d  ON d.service_id = rt.service_id AND d.deploy_seq = rt.deploy_seq
JOIN     service     s  ON s.service_id = d.service_id
WHERE    d.is_active = TRUE
GROUP BY s.service_name, d.environment
ORDER BY FIELD(d.environment,'PROD','STAGING','DEV'), hops;
*/

-- Q2  PRUNE — declared but never executed (AXIOM runtime evidence).
--     Deleting these removes whole subtrees and every CVE inside them.
/*
SELECT   s.service_name, p.package_name, rt.version_string, rt.depth
FROM     resolves_to rt
JOIN     deployment  d ON d.service_id = rt.service_id AND d.deploy_seq = rt.deploy_seq
JOIN     service     s ON s.service_id = d.service_id
JOIN     package     p ON p.package_id = rt.package_id
LEFT JOIN observes   o ON o.service_id = rt.service_id
                      AND o.deploy_seq = rt.deploy_seq
                      AND o.package_id = rt.package_id
                      AND o.version_string = rt.version_string
                      AND o.is_live = TRUE
WHERE    d.environment = 'PROD' AND d.is_active = TRUE
  AND    o.package_id IS NULL                 -- observed, never executed
ORDER BY s.service_name, rt.depth;
*/

-- Q3  MINIMUM REMEDIATION — greedy set cover, step 1.
--     Which single upgrade clears the most exposed services?
/*
SELECT   p.package_name,
         rt.version_string                       AS current_version,
         MIN(a.fixed_in)                         AS upgrade_to,
         COUNT(DISTINCT s.service_id)            AS services_fixed,
         COUNT(DISTINCT v.vuln_id)               AS vulns_cleared
FROM     affects        a
JOIN     vulnerability  v  ON v.vuln_id = a.vuln_id
JOIN     package_version pv ON pv.package_id = a.package_id
                           AND pv.version_string = a.version_string
JOIN     package        p  ON p.package_id = pv.package_id
JOIN     resolves_to    rt ON rt.package_id = pv.package_id
                          AND rt.version_string = pv.version_string
JOIN     deployment     d  ON d.service_id = rt.service_id
                          AND d.deploy_seq = rt.deploy_seq
JOIN     service        s  ON s.service_id = d.service_id
WHERE    d.environment = 'PROD' AND d.is_active = TRUE
  AND    a.fixed_in IS NOT NULL
GROUP BY p.package_name, rt.version_string
ORDER BY services_fixed DESC, vulns_cleared DESC;
*/

-- Q4  LICENSE PROPAGATION — copyleft reaching a shipped service.
/*
SELECT   s.service_name, p.package_name, l.spdx_id, rt.depth
FROM     resolves_to     rt
JOIN     deployment      d  ON d.service_id = rt.service_id AND d.deploy_seq = rt.deploy_seq
JOIN     service         s  ON s.service_id = d.service_id
JOIN     package_version pv ON pv.package_id = rt.package_id
                           AND pv.version_string = rt.version_string
JOIN     package         p  ON p.package_id = pv.package_id
JOIN     license         l  ON l.license_id = pv.license_id
WHERE    l.is_copyleft = TRUE AND d.environment = 'PROD'
ORDER BY s.service_name, rt.depth;
*/

-- Q5  CONSOLIDATE — functionally redundant packages (ReqCluster).
/*
SELECT * FROM v_cluster_redundancy ORDER BY member_count DESC;
*/
