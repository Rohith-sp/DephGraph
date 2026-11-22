-- View and index fixes (001-007 are immutable). Views keep their column names/order/types, so
-- CREATE OR REPLACE works and the 004 grants survive.

-- v_prunable: same rule as the app's prunable_rows (queries.py): latest deployment per service only,
-- the package must have at least one observes row, and none of them may be live.
-- (002 listed every resolved package of every deployment, and a never-scanned deployment made all of
-- its packages "prunable".) One row per (package, version, service).
CREATE OR REPLACE VIEW v_prunable WITH (security_invoker = true) AS
SELECT
  p.name              AS package_name,
  rt.version_string,
  s.name              AS service_name,
  s.service_id,
  rt.depth            AS dependency_depth
FROM (SELECT service_id, MAX(deploy_seq) AS deploy_seq FROM deployment GROUP BY service_id) ld
JOIN resolves_to rt ON (rt.service_id, rt.deploy_seq) = (ld.service_id, ld.deploy_seq)
JOIN package p      ON p.package_id = rt.package_id
JOIN service s      ON s.service_id = rt.service_id
JOIN observes o     ON (o.service_id, o.deploy_seq, o.package_id, o.version_string)
                     = (rt.service_id, rt.deploy_seq, rt.package_id, rt.version_string)
GROUP BY p.package_id, p.name, rt.version_string, s.service_id, s.name, rt.depth
HAVING NOT bool_or(o.is_live);

-- v_blast_radius: 006's UNION still repeats a (package, version) reached at different depths.
-- One row per package version, at its shallowest depth.
CREATE OR REPLACE VIEW v_blast_radius WITH (security_invoker = true) AS
WITH RECURSIVE closure AS (
  SELECT package_id AS pkg_id, version_string AS ver, 0 AS depth
  FROM package_version
  WHERE (package_id, version_string) IN (SELECT package_id, version_string FROM affects)
  UNION
  SELECT d.dep_package_id, d.dep_version_string, c.depth + 1
  FROM depends_on d
  JOIN closure c ON c.pkg_id = d.ref_package_id AND c.ver = d.ref_version_string
  WHERE c.depth < 10
)
SELECT pkg_id, ver, MIN(depth) AS depth FROM closure GROUP BY pkg_id, ver;

-- v_abandonment: same result as 007, but the maintainer aggregate is computed per package (LATERAL)
-- instead of over all of maintained_by for every PR-gate lookup.
CREATE OR REPLACE VIEW v_abandonment WITH (security_invoker = true) AS
SELECT
  p.name,
  pv.version_string,
  p.ecosystem,
  LEAST(100, GREATEST(0,
    CASE
      WHEN mt.last_active_at IS NOT NULL THEN                      -- A) maintainer data
        CASE WHEN mt.maintainer_count <= 1 THEN 50 ELSE 0 END
        + LEAST(GREATEST(CURRENT_DATE - mt.last_active_at::date, 0), 730) / 14.6
      ELSE                                                          -- B) fallback (002 formula)
        COALESCE(EXTRACT(EPOCH FROM now() - pv.published_at) / 31557600 * 15, 50)
        - COALESCE(mt.maintainer_count, 0) * 8
    END
    + CASE WHEN pv.is_yanked THEN 40 ELSE 0 END
  ))::INT AS abandonment_score
FROM package p
JOIN package_version pv ON p.package_id = pv.package_id
LEFT JOIN LATERAL (
  SELECT COUNT(*) AS maintainer_count, MAX(m.last_active_at) AS last_active_at
  FROM maintained_by mb
  JOIN maintainer m ON m.maintainer_id = mb.maintainer_id
  WHERE mb.package_id = p.package_id
  HAVING COUNT(*) > 0          -- no maintainers -> no row, like the old GROUP BY (mt.* NULL)
) mt ON TRUE;

-- Redundant indexes: each is a duplicate or a leading prefix of a PK/UNIQUE index.
DROP INDEX IF EXISTS idx_package_ecosystem_name;   -- = UNIQUE package_ecosystem_name_key
DROP INDEX IF EXISTS idx_resolves_to_service;      -- prefix of resolves_to_pkey
DROP INDEX IF EXISTS idx_pr_risk_verdict_service;  -- prefix of pr_risk_verdict_pkey

-- Missing FK indexes (cascade deletes from these parents seq-scanned the child tables).
CREATE INDEX IF NOT EXISTS idx_observes_scan_id        ON observes(scan_id);
CREATE INDEX IF NOT EXISTS idx_observes_pkg_ver        ON observes(package_id, version_string);
CREATE INDEX IF NOT EXISTS idx_maintained_by_maintainer ON maintained_by(maintainer_id);
CREATE INDEX IF NOT EXISTS idx_resolves_vuln_id        ON resolves(vuln_id);
CREATE INDEX IF NOT EXISTS idx_remediation_plan_team   ON remediation_plan(team_id);
