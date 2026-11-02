-- View fixes (001-005 are immutable). Column names/order unchanged, so CREATE OR REPLACE works.

-- UNION (not UNION ALL): diamond-shaped graphs no longer multiply rows per path.
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
SELECT DISTINCT pkg_id, ver, depth FROM closure;

-- Latest deployment per service only; one row per package version (shallowest depth).
CREATE OR REPLACE VIEW v_copyleft_risk WITH (security_invoker = true) AS
SELECT
  rt.service_id,
  s.name  AS service_name,
  p.name  AS package_name,
  pv.version_string,
  l.spdx_id,
  MIN(rt.depth) AS depth
FROM (SELECT service_id, MAX(deploy_seq) AS deploy_seq FROM deployment GROUP BY service_id) ld
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
JOIN (SELECT service_id, MAX(deploy_seq) AS deploy_seq FROM deployment GROUP BY service_id) ld
                        ON (rt.service_id, rt.deploy_seq) = (ld.service_id, ld.deploy_seq)
JOIN service s          ON rt.service_id = s.service_id
GROUP BY sc.cluster_id, sc.label, s.service_id, s.name
HAVING COUNT(DISTINCT p.package_id) > 1;
