-- 1. BLAST RADIUS: transitive vulnerability exposure via recursive CTE
CREATE OR REPLACE VIEW v_blast_radius WITH (security_invoker = true) AS
WITH RECURSIVE closure AS (
  SELECT package_id AS pkg_id, version_string AS ver, 0 AS depth
  FROM package_version
  WHERE (package_id, version_string) IN (SELECT package_id, version_string FROM affects)
  UNION ALL
  SELECT d.dep_package_id, d.dep_version_string, c.depth + 1
  FROM depends_on d
  JOIN closure c ON c.pkg_id = d.ref_package_id AND c.ver = d.ref_version_string
  WHERE c.depth < 10
)
SELECT DISTINCT pkg_id, ver, depth FROM closure;

-- 2. PRUNABLE PACKAGES: in resolves_to but never observed alive
CREATE OR REPLACE VIEW v_prunable WITH (security_invoker = true) AS
SELECT
  p.name              AS package_name,
  pv.version_string,
  s.name              AS service_name,
  s.service_id,
  rt.depth            AS dependency_depth
FROM package p
JOIN package_version pv ON p.package_id = pv.package_id
JOIN resolves_to rt     ON (rt.package_id, rt.version_string) = (pv.package_id, pv.version_string)
JOIN deployment d       ON (rt.service_id, rt.deploy_seq) = (d.service_id, d.deploy_seq)
JOIN service s          ON d.service_id = s.service_id
WHERE NOT EXISTS (
  SELECT 1 FROM observes o
  WHERE o.package_id     = pv.package_id
    AND o.version_string = pv.version_string
    AND o.service_id     = rt.service_id
    AND o.deploy_seq     = rt.deploy_seq
    AND o.is_live        = TRUE
);

-- 3. ABANDONMENT SCORE: 0-100 where HIGHER = MORE abandoned = WORSE
CREATE OR REPLACE VIEW v_abandonment WITH (security_invoker = true) AS
SELECT
  p.name,
  pv.version_string,
  p.ecosystem,
  LEAST(100, GREATEST(0,
    COALESCE(EXTRACT(EPOCH FROM now() - pv.published_at) / 31557600 * 15, 50)
    + CASE WHEN pv.is_yanked THEN 40 ELSE 0 END
    - (SELECT COUNT(*) FROM maintained_by mb WHERE mb.package_id = p.package_id) * 8
  ))::INT AS abandonment_score
FROM package p
JOIN package_version pv ON p.package_id = pv.package_id;

-- 4. LICENSE PROPAGATION: copyleft contamination via transitive closure
CREATE OR REPLACE VIEW v_copyleft_risk WITH (security_invoker = true) AS
SELECT DISTINCT
  rt.service_id,
  s.name  AS service_name,
  p.name  AS package_name,
  pv.version_string,
  l.spdx_id,
  rt.depth
FROM resolves_to rt
JOIN package_version pv ON (rt.package_id, rt.version_string) = (pv.package_id, pv.version_string)
JOIN package p          ON pv.package_id = p.package_id
JOIN license l          ON pv.license_id = l.license_id
JOIN service s          ON rt.service_id = s.service_id
WHERE l.is_copyleft = TRUE;

-- 5. CLUSTER REDUNDANCY: multiple packages same function in same service
CREATE OR REPLACE VIEW v_cluster_redundancy WITH (security_invoker = true) AS
SELECT
  sc.label            AS cluster_label,
  s.name              AS service_name,
  COUNT(DISTINCT p.package_id) AS redundant_package_count,
  ARRAY_AGG(DISTINCT p.name ORDER BY p.name) AS packages
FROM semantic_cluster sc
JOIN package_version pv ON pv.cluster_id = sc.cluster_id
JOIN package p          ON pv.package_id = p.package_id
JOIN resolves_to rt     ON (rt.package_id, rt.version_string) = (pv.package_id, pv.version_string)
JOIN service s          ON rt.service_id = s.service_id
GROUP BY sc.cluster_id, sc.label, s.service_id, s.name
HAVING COUNT(DISTINCT p.package_id) > 1;
