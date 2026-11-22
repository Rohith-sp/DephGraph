-- GitHub Issue alerts + auto-import bookkeeping (001-012 are immutable). Depends on 012: the code
-- using these tables reads service.github_installation_id / github_repo_id / root_path.
--
-- alert_issue: one GitHub issue per (service, vulnerability), so a vuln that stays exposed is
-- announced once. State 'opening' is a placeholder written before the issue is created (a crash
-- between the POST and the insert cannot create a second issue). repo / installation_id are copied
-- onto the row and service_id is SET NULL on service deletion, so an orphaned issue still gets
-- closed. alert_skip: repos the App cannot write to right now (not installed, forbidden), retried
-- after `until` so they do not starve the others. service_import: one row per (service, commit);
-- 'importing' is a claim (stale after 10 minutes), 'done' is final. System tables: RLS on, no policy,
-- no grant to authenticated.
ALTER TABLE team
  ADD COLUMN alert_min_severity TEXT    NOT NULL DEFAULT 'HIGH'
    CHECK (alert_min_severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
  ADD COLUMN alerts_enabled     BOOLEAN NOT NULL DEFAULT false;

CREATE TABLE alert_issue (
  alert_id        BIGSERIAL   PRIMARY KEY,
  service_id      BIGINT      REFERENCES service(service_id) ON DELETE SET NULL,
  vuln_id         BIGINT      NOT NULL REFERENCES vulnerability(vuln_id) ON DELETE CASCADE,
  repo            TEXT        NOT NULL,
  installation_id BIGINT,
  issue_number    INT,
  state           TEXT        NOT NULL DEFAULT 'opening' CHECK (state IN ('opening', 'open', 'closed')),
  opened_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  closed_at       TIMESTAMPTZ,
  UNIQUE (service_id, vuln_id)
);
ALTER TABLE alert_issue ENABLE ROW LEVEL SECURITY;

CREATE TABLE alert_skip (
  repo  TEXT        PRIMARY KEY,
  until TIMESTAMPTZ NOT NULL
);
ALTER TABLE alert_skip ENABLE ROW LEVEL SECURITY;

CREATE TABLE service_import (
  service_id BIGINT      NOT NULL REFERENCES service(service_id) ON DELETE CASCADE,
  sha        TEXT        NOT NULL,
  status     TEXT        NOT NULL DEFAULT 'importing' CHECK (status IN ('importing', 'done')),
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (service_id, sha)
);
ALTER TABLE service_import ENABLE ROW LEVEL SECURITY;
