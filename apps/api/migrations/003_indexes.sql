-- FK indexes (PostgreSQL does NOT auto-index FKs)
CREATE INDEX idx_service_team_id         ON service(team_id);
CREATE INDEX idx_scan_run_service_id     ON scan_run(service_id);
CREATE INDEX idx_package_version_license ON package_version(license_id);
CREATE INDEX idx_package_version_cluster ON package_version(cluster_id);
CREATE INDEX idx_resolves_to_service     ON resolves_to(service_id, deploy_seq);
CREATE INDEX idx_resolves_to_pkg_ver     ON resolves_to(package_id, version_string);
CREATE INDEX idx_depends_on_ref          ON depends_on(ref_package_id, ref_version_string);
CREATE INDEX idx_affects_vuln_id         ON affects(vuln_id);
CREATE INDEX idx_observes_live           ON observes(service_id, deploy_seq) WHERE is_live = TRUE;
CREATE INDEX idx_waived_for_service      ON waived_for(service_id);

-- Query-pattern indexes
CREATE INDEX idx_vulnerability_severity  ON vulnerability(severity);
CREATE INDEX idx_vulnerability_cvss      ON vulnerability(cvss_score DESC);
CREATE INDEX idx_package_ecosystem_name  ON package(ecosystem, name);
CREATE INDEX idx_pr_risk_verdict_service ON pr_risk_verdict(service_id, pr_number);
