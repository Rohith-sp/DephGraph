-- =====================================================================
-- DephGraph — Phase 4 SQL queries, alternative version (MySQL 8.4)
-- The main Q1-Q7 file is sql/queries.sql (matches relational-algebra/README.md);
-- this one is kept for its different query formulations.
--
-- Run after er-diagram/schema.sql and sql/seed.sql:
--   scripts/mysql.sh run sql/queries.sql
--
-- The Q1-Q7 queries use the fixed Phase 2 sample data. Complex-query
-- examples follow. No rows are changed by this file.
-- =====================================================================

USE dephgraph;

-- ---------------------------------------------------------------------
-- Q1 — Blast radius: active deployments that reach log4j-core.
-- Expected sample result: payments-svc / PROD / 1 hop, with the
-- spring-boot-starter-log4j2 -> log4j-core path.
-- Recursive upward walk follows "who depends on this version?"
-- ---------------------------------------------------------------------
WITH RECURSIVE reachable (package_id, version_string, depth, path, visited) AS (
    SELECT pv.package_id, pv.version_string, 0,
           CAST(CONCAT(p.package_name, '@', pv.version_string) AS CHAR(2000)),
           JSON_ARRAY(CONCAT(pv.package_id, ':', pv.version_string))
    FROM package_version pv
    JOIN package p ON p.package_id = pv.package_id
    WHERE p.package_name = 'log4j-core'

    UNION ALL

    SELECT d.dependent_package_id, d.dependent_version, r.depth + 1,
           CONCAT(p2.package_name, '@', d.dependent_version, ' -> ', r.path),
           JSON_ARRAY_APPEND(
               r.visited, '$', CONCAT(d.dependent_package_id, ':', d.dependent_version)
           )
    FROM reachable r
    JOIN depends_on d
      ON d.dependency_package_id = r.package_id
     AND d.dependency_version = r.version_string
    JOIN package p2 ON p2.package_id = d.dependent_package_id
    WHERE r.depth < 15
      AND NOT JSON_CONTAINS(
          r.visited,
          JSON_QUOTE(CONCAT(d.dependent_package_id, ':', d.dependent_version))
      )
),
ranked AS (
    SELECT s.service_name, d.environment, r.depth, r.path,
           ROW_NUMBER() OVER (
               PARTITION BY s.service_id, d.environment
               ORDER BY r.depth DESC, r.path
           ) AS path_rank
    FROM reachable r
    JOIN resolves_to rt
      ON rt.package_id = r.package_id
     AND rt.version_string = r.version_string
    JOIN deployment d
      ON d.service_id = rt.service_id
     AND d.deploy_seq = rt.deploy_seq
    JOIN service s ON s.service_id = d.service_id
    WHERE d.is_active = TRUE
)
SELECT service_name, environment, depth AS hops, path AS dependency_path
FROM ranked
WHERE path_rank = 1
ORDER BY FIELD(environment, 'PROD', 'STAGING', 'DEV'), service_name;

-- ---------------------------------------------------------------------
-- Q2 — Prune: production dependencies never observed executing.
-- Expected sample rows: spring-boot-starter-log4j2 and logback-classic.
-- ---------------------------------------------------------------------
SELECT s.service_name, p.package_name, rt.version_string, rt.depth
FROM resolves_to rt
JOIN deployment d
  ON d.service_id = rt.service_id AND d.deploy_seq = rt.deploy_seq
JOIN service s ON s.service_id = d.service_id
JOIN package p ON p.package_id = rt.package_id
LEFT JOIN observes o
  ON o.service_id = rt.service_id
 AND o.deploy_seq = rt.deploy_seq
 AND o.package_id = rt.package_id
 AND o.version_string = rt.version_string
 AND o.is_live = TRUE
WHERE d.environment = 'PROD'
  AND d.is_active = TRUE
  AND o.package_id IS NULL
ORDER BY s.service_name, rt.depth, p.package_name;

-- ---------------------------------------------------------------------
-- Q3 — One greedy remediation step: exposed service and vulnerability
-- coverage per affected package version. MAX(fixed_in) is a useful
-- minimum target for the sample's monotonic version strings; in a
-- general version catalog, compare parsed versions, not strings.
-- Expected sample: log4j-core 2.14.1, 1 service, 2 vulnerabilities.
-- ---------------------------------------------------------------------
SELECT p.package_name,
       rt.version_string AS current_version,
       MAX(a.fixed_in) AS minimum_fix_for_all_listed_vulns,
       COUNT(DISTINCT s.service_id) AS services_fixed,
       COUNT(DISTINCT v.vuln_id) AS vulns_cleared
FROM affects a
JOIN vulnerability v ON v.vuln_id = a.vuln_id
JOIN package p ON p.package_id = a.package_id
JOIN resolves_to rt
  ON rt.package_id = a.package_id
 AND rt.version_string = a.version_string
JOIN deployment d
  ON d.service_id = rt.service_id AND d.deploy_seq = rt.deploy_seq
JOIN service s ON s.service_id = d.service_id
WHERE d.environment = 'PROD'
  AND d.is_active = TRUE
  AND a.fixed_in IS NOT NULL
GROUP BY p.package_id, p.package_name, rt.version_string
ORDER BY services_fixed DESC, vulns_cleared DESC, p.package_name;

-- ---------------------------------------------------------------------
-- Q4 — Copyleft license propagation to deployed package versions.
-- Expected sample row: payments-svc / logback-classic / LGPL-2.1-only.
-- ---------------------------------------------------------------------
SELECT s.service_name, p.package_name, rt.version_string, l.spdx_id, rt.depth
FROM resolves_to rt
JOIN deployment d
  ON d.service_id = rt.service_id AND d.deploy_seq = rt.deploy_seq
JOIN service s ON s.service_id = d.service_id
JOIN package p ON p.package_id = rt.package_id
JOIN package_version pv
  ON pv.package_id = rt.package_id
 AND pv.version_string = rt.version_string
JOIN license l ON l.license_id = pv.license_id
WHERE l.is_copyleft = TRUE
  AND d.environment = 'PROD'
  AND d.is_active = TRUE
ORDER BY s.service_name, rt.depth, p.package_name;

-- ---------------------------------------------------------------------
-- Q5 — Cluster redundancy: clusters containing multiple packages.
-- Expected sample: logging, with log4j-core and logback-classic.
-- ---------------------------------------------------------------------
SELECT cluster_id, cluster_label, member_count, packages
FROM v_cluster_redundancy
ORDER BY member_count DESC, cluster_label;

-- ---------------------------------------------------------------------
-- Q6 — Division: maintainers who own every package in the logging
-- cluster. Expected sample: no rows.
-- ---------------------------------------------------------------------
SELECT m.maintainer_id, m.handle
FROM maintainer m
WHERE EXISTS (
    SELECT 1
    FROM package p
    JOIN semantic_cluster sc ON sc.cluster_id = p.cluster_id
    WHERE sc.cluster_label = 'logging'
)
AND NOT EXISTS (
    SELECT 1
    FROM package p
    JOIN semantic_cluster sc ON sc.cluster_id = p.cluster_id
    WHERE sc.cluster_label = 'logging'
      AND NOT EXISTS (
          SELECT 1
          FROM maintained_by mb
          WHERE mb.package_id = p.package_id
            AND mb.maintainer_id = m.maintainer_id
      )
)
ORDER BY m.handle;

-- ---------------------------------------------------------------------
-- Q7a/Q7b — Exposures that are waived vs still open.
-- The fixed reference date keeps the teaching result reproducible.
-- Expected waived: (901, 10); open: (900, 10).
-- ---------------------------------------------------------------------
WITH exposure AS (
    SELECT DISTINCT a.vuln_id, rt.service_id
    FROM resolves_to rt
    JOIN deployment d
      ON d.service_id = rt.service_id AND d.deploy_seq = rt.deploy_seq
    JOIN affects a
      ON a.package_id = rt.package_id
     AND a.version_string = rt.version_string
    WHERE d.environment = 'PROD' AND d.is_active = TRUE
),
waived AS (
    SELECT vuln_id, service_id
    FROM waived_for
    WHERE expires_on >= DATE '2026-11-02'
)
SELECT 'waived' AS exposure_status, e.vuln_id, e.service_id
FROM exposure e
JOIN waived w USING (vuln_id, service_id)
UNION ALL
SELECT 'open', e.vuln_id, e.service_id
FROM exposure e
WHERE NOT EXISTS (
    SELECT 1 FROM waived w
    WHERE w.vuln_id = e.vuln_id AND w.service_id = e.service_id
)
ORDER BY exposure_status, vuln_id, service_id;

-- Q7c — Packages requiring review because they are copyleft OR vulnerable.
SELECT package_id
FROM (
    SELECT DISTINCT pv.package_id
    FROM package_version pv
    JOIN license l ON l.license_id = pv.license_id
    WHERE l.is_copyleft = TRUE
    UNION
    SELECT DISTINCT package_id FROM affects
) AS review_packages
ORDER BY package_id;

-- =====================================================================
-- Complex SQL patterns (same sample data; read-only)
-- =====================================================================

-- Nested and correlated subquery: packages with at least two known
-- vulnerability records affecting a version.
SELECT p.package_id, p.package_name
FROM package p
WHERE (
    SELECT COUNT(DISTINCT a.vuln_id)
    FROM affects a
    WHERE a.package_id = p.package_id
) >= 2
ORDER BY p.package_name;

-- EXISTS: services with at least one production vulnerability exposure.
SELECT s.service_id, s.service_name
FROM service s
WHERE EXISTS (
    SELECT 1
    FROM deployment d
    JOIN resolves_to rt
      ON rt.service_id = d.service_id AND rt.deploy_seq = d.deploy_seq
    JOIN affects a
      ON a.package_id = rt.package_id AND a.version_string = rt.version_string
    WHERE d.service_id = s.service_id
      AND d.environment = 'PROD'
      AND d.is_active = TRUE
)
ORDER BY s.service_name;

-- NOT EXISTS: packages with no affected versions.
SELECT p.package_id, p.package_name
FROM package p
WHERE NOT EXISTS (
    SELECT 1 FROM affects a WHERE a.package_id = p.package_id
)
ORDER BY p.package_name;

-- IN: vulnerable versions present in an active production deployment.
SELECT p.package_name, rt.version_string
FROM resolves_to rt
JOIN deployment d
  ON d.service_id = rt.service_id AND d.deploy_seq = rt.deploy_seq
JOIN package p ON p.package_id = rt.package_id
WHERE d.environment = 'PROD'
  AND d.is_active = TRUE
  AND (rt.package_id, rt.version_string) IN (
      SELECT package_id, version_string FROM affects
  )
ORDER BY p.package_name;

-- ALL: versions published later than every affected version's publish
-- date. This is a date comparison, not a version-string comparison.
SELECT p.package_name, pv.version_string, pv.published_at
FROM package_version pv
JOIN package p ON p.package_id = pv.package_id
WHERE pv.published_at > ALL (
    SELECT affected_version.published_at
    FROM affects a
    JOIN package_version affected_version
      ON affected_version.package_id = a.package_id
     AND affected_version.version_string = a.version_string
    WHERE affected_version.published_at IS NOT NULL
)
ORDER BY pv.published_at, p.package_name, pv.version_string;

-- ANY: versions published later than at least one affected version date.
SELECT p.package_name, pv.version_string, pv.published_at
FROM package_version pv
JOIN package p ON p.package_id = pv.package_id
WHERE pv.published_at > ANY (
    SELECT affected_version.published_at
    FROM affects a
    JOIN package_version affected_version
      ON affected_version.package_id = a.package_id
     AND affected_version.version_string = a.version_string
    WHERE affected_version.published_at IS NOT NULL
)
ORDER BY pv.published_at, p.package_name, pv.version_string;

-- Outer join: list every team, including teams without a production
-- service. COUNT(service_id), not COUNT(*), keeps empty teams at zero.
SELECT t.team_name, COUNT(DISTINCT d.service_id) AS production_services
FROM team t
LEFT JOIN service s ON s.team_id = t.team_id
LEFT JOIN deployment d
  ON d.service_id = s.service_id
 AND d.environment = 'PROD'
 AND d.is_active = TRUE
GROUP BY t.team_id, t.team_name
ORDER BY t.team_name;

-- GROUP BY / HAVING: packages affected by multiple advisories.
SELECT p.package_name, COUNT(DISTINCT a.vuln_id) AS advisory_count
FROM package p
JOIN affects a ON a.package_id = p.package_id
GROUP BY p.package_id, p.package_name
HAVING COUNT(DISTINCT a.vuln_id) > 1
ORDER BY advisory_count DESC, p.package_name;

-- Existing views: derived service exposure and cluster redundancy.
SELECT service_name, open_vulns, critical_vulns, avg_cvss
FROM v_service_exposure
ORDER BY service_name;

SELECT cluster_label, member_count, packages
FROM v_cluster_redundancy
ORDER BY cluster_label;
