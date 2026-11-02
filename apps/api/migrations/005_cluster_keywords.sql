-- Phase 5/6 additions (001-004 are immutable).
ALTER TABLE semantic_cluster ADD COLUMN IF NOT EXISTS keywords TEXT[] NOT NULL DEFAULT '{}';

-- Stage 2/3 explanation shown on the dashboard.
ALTER TABLE pr_risk_verdict ADD COLUMN IF NOT EXISTS reasoning TEXT;

-- Same view, ids appended (CREATE OR REPLACE VIEW may only add trailing columns).
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
JOIN service s          ON rt.service_id = s.service_id
GROUP BY sc.cluster_id, sc.label, s.service_id, s.name
HAVING COUNT(DISTINCT p.package_id) > 1;
