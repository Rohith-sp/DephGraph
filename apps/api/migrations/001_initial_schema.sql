-- Entities (14) + relationships (9) = 23 tables. Never edit; add 00N migrations.
-- ╔══════════════════════════════════════╗
-- ║   STRONG ENTITIES (9)                ║
-- ╚══════════════════════════════════════╝

CREATE TABLE team (
  team_id    BIGSERIAL    PRIMARY KEY,
  name       VARCHAR(120) NOT NULL UNIQUE,
  slug       VARCHAR(80)  NOT NULL UNIQUE,
  created_at TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE service (
  service_id BIGSERIAL    PRIMARY KEY,
  team_id    BIGINT       NOT NULL REFERENCES team(team_id) ON DELETE CASCADE,
  name       VARCHAR(120) NOT NULL,
  repo_url   VARCHAR(500),
  language   VARCHAR(40),
  created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
  UNIQUE(team_id, name)
);

CREATE TABLE package (
  package_id  BIGSERIAL    PRIMARY KEY,
  ecosystem   VARCHAR(40)  NOT NULL CHECK (ecosystem IN ('npm','pypi','cargo','go','maven','nuget')),
  name        VARCHAR(255) NOT NULL,
  description TEXT,
  created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
  UNIQUE(ecosystem, name)
);

CREATE TABLE maintainer (
  maintainer_id BIGSERIAL    PRIMARY KEY,
  username      VARCHAR(120) NOT NULL,
  platform      VARCHAR(40)  NOT NULL DEFAULT 'github',
  profile_url   VARCHAR(500),
  UNIQUE(platform, username)
);

CREATE TABLE semantic_cluster (
  cluster_id   BIGSERIAL    PRIMARY KEY,
  label        VARCHAR(120) NOT NULL,
  centroid_vec JSONB,
  member_count INT          NOT NULL DEFAULT 0,
  created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE vulnerability (
  vuln_id      BIGSERIAL    PRIMARY KEY,
  osv_id       VARCHAR(40)  NOT NULL UNIQUE,
  severity     VARCHAR(20)  CHECK (severity IN ('CRITICAL','HIGH','MEDIUM','LOW','NONE')),
  cvss_score   NUMERIC(4,2),
  title        VARCHAR(500),
  published_at TIMESTAMPTZ,
  modified_at  TIMESTAMPTZ
);

CREATE TABLE license (
  license_id  BIGSERIAL   PRIMARY KEY,
  spdx_id     VARCHAR(60) NOT NULL UNIQUE,
  name        VARCHAR(200),
  is_copyleft BOOLEAN     NOT NULL DEFAULT FALSE
);

CREATE TABLE remediation_plan (
  plan_id      BIGSERIAL    PRIMARY KEY,
  team_id      BIGINT       NOT NULL REFERENCES team(team_id) ON DELETE RESTRICT, -- RAISED_BY (ER: RESTRICT)
  title        VARCHAR(255),
  coverage_pct NUMERIC(5,2) DEFAULT 0,
  created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
  status       VARCHAR(20)  NOT NULL DEFAULT 'DRAFT'
               CHECK (status IN ('DRAFT','ACTIVE','CLOSED'))
);

CREATE TABLE scan_run (
  scan_id      BIGSERIAL   PRIMARY KEY,
  service_id   BIGINT      NOT NULL REFERENCES service(service_id) ON DELETE CASCADE,
  started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ,
  source       VARCHAR(20) NOT NULL DEFAULT 'manual'
               CHECK (source IN ('manual','pr_hook','scheduled','cli'))
);

-- ╔══════════════════════════════════════╗
-- ║   WEAK ENTITIES (5)                  ║
-- ╚══════════════════════════════════════╝

-- DEPLOYMENT: weak, discriminator = deploy_seq within service_id
CREATE TABLE deployment (
  service_id   BIGINT      NOT NULL REFERENCES service(service_id) ON DELETE CASCADE,
  deploy_seq   INT         NOT NULL,
  environment  VARCHAR(40) NOT NULL DEFAULT 'production',
  deployed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (service_id, deploy_seq)
);

-- PACKAGE_VERSION: weak, discriminator = version_string within package_id
CREATE TABLE package_version (
  package_id     BIGINT       NOT NULL REFERENCES package(package_id) ON DELETE CASCADE,
  version_string VARCHAR(100) NOT NULL,
  license_id     BIGINT       REFERENCES license(license_id) ON DELETE SET NULL,
  cluster_id     BIGINT       REFERENCES semantic_cluster(cluster_id) ON DELETE SET NULL,
  published_at   TIMESTAMPTZ,
  is_yanked      BOOLEAN      NOT NULL DEFAULT FALSE,
  PRIMARY KEY (package_id, version_string)
);

-- REMEDIATION_ACTION: weak, discriminator = action_seq within plan_id
CREATE TABLE remediation_action (
  plan_id     BIGINT      NOT NULL REFERENCES remediation_plan(plan_id) ON DELETE CASCADE,
  action_seq  INT         NOT NULL,
  description TEXT,
  status      VARCHAR(20) NOT NULL DEFAULT 'PENDING'
              CHECK (status IN ('PENDING','DONE','SKIPPED')),
  PRIMARY KEY (plan_id, action_seq)
);

-- PR_REVIEW: weak, discriminator = pr_number within service_id
CREATE TABLE pr_review (
  service_id BIGINT       NOT NULL REFERENCES service(service_id) ON DELETE CASCADE,
  pr_number  INT          NOT NULL,
  pr_url     VARCHAR(500),
  author     VARCHAR(120),
  opened_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
  PRIMARY KEY (service_id, pr_number)
);

-- PR_RISK_VERDICT: weak entity of PR_REVIEW, discriminator = dep_name within PR
CREATE TABLE pr_risk_verdict (
  service_id    BIGINT       NOT NULL,
  pr_number     INT          NOT NULL,
  dep_name      VARCHAR(255) NOT NULL,
  risk_level    VARCHAR(20)  NOT NULL CHECK (risk_level IN ('PASS','ADVISORY','BLOCK')),
  stage_reached INT          NOT NULL CHECK (stage_reached BETWEEN 1 AND 3),
  context_tokens INT,
  decided_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
  PRIMARY KEY (service_id, pr_number, dep_name),
  FOREIGN KEY (service_id, pr_number)
    REFERENCES pr_review(service_id, pr_number) ON DELETE CASCADE
);

-- ╔══════════════════════════════════════╗
-- ║   M:N RELATIONSHIP TABLES (6)        ║
-- ╚══════════════════════════════════════╝

-- MAINTAINED_BY (PACKAGE ↔ MAINTAINER)
CREATE TABLE maintained_by (
  package_id    BIGINT NOT NULL REFERENCES package(package_id) ON DELETE CASCADE,
  maintainer_id BIGINT NOT NULL REFERENCES maintainer(maintainer_id) ON DELETE CASCADE,
  role          VARCHAR(40) NOT NULL DEFAULT 'owner',
  PRIMARY KEY (package_id, maintainer_id)
);

-- RESOLVES_TO — materialized transitive closure (DEPLOYMENT ↔ PACKAGE_VERSION)
-- NOT computed on-the-fly; maintained by background job (cites Agrawal/Borgida 1989)
CREATE TABLE resolves_to (
  service_id     BIGINT       NOT NULL,
  deploy_seq     INT          NOT NULL,
  package_id     BIGINT       NOT NULL,
  version_string VARCHAR(100) NOT NULL,
  depth          INT          NOT NULL DEFAULT 0,
  is_direct      BOOLEAN      NOT NULL DEFAULT FALSE,
  PRIMARY KEY (service_id, deploy_seq, package_id, version_string),
  FOREIGN KEY (service_id, deploy_seq)
    REFERENCES deployment(service_id, deploy_seq) ON DELETE CASCADE,
  FOREIGN KEY (package_id, version_string)
    REFERENCES package_version(package_id, version_string) ON DELETE CASCADE
);

-- DEPENDS_ON — recursive M:N self-join on PACKAGE_VERSION
-- roles: "dependent" (left) and "dependency" (right)
CREATE TABLE depends_on (
  dep_package_id     BIGINT       NOT NULL,
  dep_version_string VARCHAR(100) NOT NULL,
  ref_package_id     BIGINT       NOT NULL,
  ref_version_string VARCHAR(100) NOT NULL,
  scope              VARCHAR(20)  CHECK (scope IN ('runtime','dev','optional','peer')),
  PRIMARY KEY (dep_package_id, dep_version_string, ref_package_id, ref_version_string),
  FOREIGN KEY (dep_package_id, dep_version_string)
    REFERENCES package_version(package_id, version_string) ON DELETE CASCADE,
  FOREIGN KEY (ref_package_id, ref_version_string)
    REFERENCES package_version(package_id, version_string) ON DELETE CASCADE
);

-- AFFECTS (PACKAGE_VERSION ↔ VULNERABILITY)
CREATE TABLE affects (
  package_id     BIGINT       NOT NULL,
  version_string VARCHAR(100) NOT NULL,
  vuln_id        BIGINT       NOT NULL REFERENCES vulnerability(vuln_id) ON DELETE CASCADE,
  fixed_in       VARCHAR(100),
  PRIMARY KEY (package_id, version_string, vuln_id),
  FOREIGN KEY (package_id, version_string)
    REFERENCES package_version(package_id, version_string) ON DELETE CASCADE
);

-- RESOLVES (REMEDIATION_ACTION ↔ VULNERABILITY)
CREATE TABLE resolves (
  plan_id    BIGINT NOT NULL,
  action_seq INT    NOT NULL,
  vuln_id    BIGINT NOT NULL REFERENCES vulnerability(vuln_id) ON DELETE CASCADE,
  PRIMARY KEY (plan_id, action_seq, vuln_id),
  FOREIGN KEY (plan_id, action_seq)
    REFERENCES remediation_action(plan_id, action_seq) ON DELETE CASCADE
);

-- WAIVED_FOR (VULNERABILITY ↔ SERVICE) — accepted risk waivers
CREATE TABLE waived_for (
  vuln_id    BIGINT NOT NULL REFERENCES vulnerability(vuln_id) ON DELETE CASCADE,
  service_id BIGINT NOT NULL REFERENCES service(service_id) ON DELETE CASCADE,
  reason     TEXT,
  expires_on DATE,
  PRIMARY KEY (vuln_id, service_id)
);

-- ╔══════════════════════════════════════╗
-- ║   TERNARY RELATIONSHIP TABLE (1)     ║
-- ╚══════════════════════════════════════╝

-- OBSERVES (DEPLOYMENT × SCAN_RUN × PACKAGE_VERSION)
-- Ternary because: "exec_count=0 during THIS scan window for THIS deployment" is the full fact.
-- Decomposing to binary loses the observation window — "zero calls in which window?" is undefined.
CREATE TABLE observes (
  service_id     BIGINT       NOT NULL,
  deploy_seq     INT          NOT NULL,
  scan_id        BIGINT       NOT NULL REFERENCES scan_run(scan_id) ON DELETE CASCADE,
  package_id     BIGINT       NOT NULL,
  version_string VARCHAR(100) NOT NULL,
  exec_count     INT          NOT NULL DEFAULT 0,
  is_live        BOOLEAN      NOT NULL DEFAULT FALSE,
  PRIMARY KEY (service_id, deploy_seq, scan_id, package_id, version_string),
  FOREIGN KEY (service_id, deploy_seq)
    REFERENCES deployment(service_id, deploy_seq) ON DELETE CASCADE,
  FOREIGN KEY (package_id, version_string)
    REFERENCES package_version(package_id, version_string) ON DELETE CASCADE
);

-- ╔══════════════════════════════════════╗
-- ║   MULTIVALUED ATTRIBUTE TABLES (2)   ║
-- ╚══════════════════════════════════════╝

CREATE TABLE package_alias (
  package_id BIGINT       NOT NULL REFERENCES package(package_id) ON DELETE CASCADE,
  alias      VARCHAR(255) NOT NULL,
  PRIMARY KEY (package_id, alias)
);

CREATE TABLE maintainer_email (
  maintainer_id BIGINT       NOT NULL REFERENCES maintainer(maintainer_id) ON DELETE CASCADE,
  email         VARCHAR(255) NOT NULL,
  is_primary    BOOLEAN      NOT NULL DEFAULT FALSE,
  PRIMARY KEY (maintainer_id, email)
);
