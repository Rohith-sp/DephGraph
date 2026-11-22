-- Per-team tenancy + production-only analysis (001-009 are immutable).
--
-- team_member maps a Supabase auth user to the teams whose data they may see. The API connects as
-- the table owner and enforces membership itself; the RLS policies below protect direct access
-- (PostgREST / the anon-key client) with the same rule.

CREATE TABLE team_member (
  team_id    BIGINT      NOT NULL REFERENCES team(team_id) ON DELETE CASCADE,
  user_id    UUID        NOT NULL,
  role       TEXT        NOT NULL DEFAULT 'member' CHECK (role IN ('member', 'admin')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (team_id, user_id)
);
CREATE INDEX idx_team_member_user ON team_member(user_id);

-- Supabase ships auth.uid(); plain Postgres (compose, CI) gets an equivalent. Never replaced.
CREATE SCHEMA IF NOT EXISTS auth;
DO $$ BEGIN
  IF to_regprocedure('auth.uid()') IS NULL THEN
    CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS $f$
      SELECT coalesce(
        nullif(current_setting('request.jwt.claim.sub', true), ''),
        nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub'
      )::uuid
    $f$;
  END IF;
END $$;
GRANT USAGE ON SCHEMA auth TO authenticated;

-- Each team-owned table is visible to members only. Tables that hang off service / plan reuse that
-- parent's policy through a subquery (RLS applies inside it too), so the rule lives in one place.
ALTER TABLE team_member ENABLE ROW LEVEL SECURITY;
GRANT SELECT ON team_member TO authenticated;
DO $$ DECLARE t TEXT; ownership TEXT; BEGIN
  FOR t, ownership IN VALUES
    ('team_member',        'user_id = auth.uid()'),
    ('team',               'team_id IN (SELECT team_id FROM team_member WHERE user_id = auth.uid())'),
    ('service',            'team_id IN (SELECT team_id FROM team)'),
    ('remediation_plan',   'team_id IN (SELECT team_id FROM team)'),
    ('deployment',         'service_id IN (SELECT service_id FROM service)'),
    ('scan_run',           'service_id IN (SELECT service_id FROM service)'),
    ('pr_review',          'service_id IN (SELECT service_id FROM service)'),
    ('pr_risk_verdict',    'service_id IN (SELECT service_id FROM service)'),
    ('waived_for',         'service_id IN (SELECT service_id FROM service)'),
    ('resolves_to',        'service_id IN (SELECT service_id FROM service)'),
    ('observes',           'service_id IN (SELECT service_id FROM service)'),
    ('remediation_action', 'plan_id IN (SELECT plan_id FROM remediation_plan)'),
    ('resolves',           'plan_id IN (SELECT plan_id FROM remediation_plan)')
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I ON %I', t || '_select_authenticated', t);
    EXECUTE format('CREATE POLICY %I ON %I FOR SELECT TO authenticated USING (%s)',
                   t || '_select_authenticated', t, ownership);
  END LOOP;
END $$;

-- Maintainer emails are PII: API only.
DROP POLICY maintainer_email_select_authenticated ON maintainer_email;
REVOKE SELECT ON maintainer_email FROM authenticated;

-- Production-only analysis: only a service's latest *production* deployment is analysed; staging and
-- preview deployments never change risk numbers. Same columns as 008 / 006, so the grants survive.
CREATE OR REPLACE VIEW v_prunable WITH (security_invoker = true) AS
SELECT
  p.name              AS package_name,
  rt.version_string,
  s.name              AS service_name,
  s.service_id,
  rt.depth            AS dependency_depth
FROM (SELECT service_id, MAX(deploy_seq) AS deploy_seq FROM deployment
      WHERE environment = 'production' GROUP BY service_id) ld
JOIN resolves_to rt ON (rt.service_id, rt.deploy_seq) = (ld.service_id, ld.deploy_seq)
JOIN package p      ON p.package_id = rt.package_id
JOIN service s      ON s.service_id = rt.service_id
JOIN observes o     ON (o.service_id, o.deploy_seq, o.package_id, o.version_string)
                     = (rt.service_id, rt.deploy_seq, rt.package_id, rt.version_string)
GROUP BY p.package_id, p.name, rt.version_string, s.service_id, s.name, rt.depth
HAVING NOT bool_or(o.is_live);

CREATE OR REPLACE VIEW v_copyleft_risk WITH (security_invoker = true) AS
SELECT
  rt.service_id,
  s.name  AS service_name,
  p.name  AS package_name,
  pv.version_string,
  l.spdx_id,
  MIN(rt.depth) AS depth
FROM (SELECT service_id, MAX(deploy_seq) AS deploy_seq FROM deployment
      WHERE environment = 'production' GROUP BY service_id) ld
JOIN resolves_to rt     ON (rt.service_id, rt.deploy_seq) = (ld.service_id, ld.deploy_seq)
JOIN package_version pv ON (rt.package_id, rt.version_string) = (pv.package_id, pv.version_string)
JOIN package p          ON pv.package_id = p.package_id
JOIN license l          ON pv.license_id = l.license_id
JOIN service s          ON rt.service_id = s.service_id
WHERE l.is_copyleft = TRUE
GROUP BY rt.service_id, s.name, p.name, pv.version_string, l.spdx_id;

CREATE OR REPLACE VIEW v_cluster_redundancy WITH (security_invoker = true) AS
SELECT
  sc.label            AS cluster_label,
  s.name              AS service_name,
  COUNT(DISTINCT p.package_id) AS redundant_package_count,
  ARRAY_AGG(DISTINCT p.name ORDER BY p.name) AS packages,
  s.service_id        AS service_id,
  sc.cluster_id       AS cluster_id
FROM semantic_cluster sc
JOIN package_version pv ON pv.cluster_id = sc.cluster_id
JOIN package p          ON pv.package_id = p.package_id
JOIN resolves_to rt     ON (rt.package_id, rt.version_string) = (pv.package_id, pv.version_string)
JOIN (SELECT service_id, MAX(deploy_seq) AS deploy_seq FROM deployment
      WHERE environment = 'production' GROUP BY service_id) ld
                        ON (rt.service_id, rt.deploy_seq) = (ld.service_id, ld.deploy_seq)
JOIN service s          ON rt.service_id = s.service_id
GROUP BY sc.cluster_id, sc.label, s.service_id, s.name
HAVING COUNT(DISTINCT p.package_id) > 1;
