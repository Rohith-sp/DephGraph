-- Per-deployment lockfile edges (001-011 are immutable).
--
-- depends_on is global catalogue data: one row says "this version depends on that version", for
-- every project. A lockfile pins the exact versions of ONE project, so its edges belong to that
-- deployment alone; writing them to depends_on made other projects' paths and closures inherit
-- them. A deployment ingested from a lockfile is flagged `locked`: its resolves_to rows are exactly
-- the lockfile's packages and its edges live here. Plain-manifest deployments keep using depends_on.
ALTER TABLE deployment ADD COLUMN locked BOOLEAN NOT NULL DEFAULT false;

CREATE TABLE deployment_edge (
  service_id         BIGINT       NOT NULL,
  deploy_seq         INT          NOT NULL,
  dep_package_id     BIGINT       NOT NULL,   -- dependent (left)
  dep_version_string VARCHAR(100) NOT NULL,
  ref_package_id     BIGINT       NOT NULL,   -- dependency (right)
  ref_version_string VARCHAR(100) NOT NULL,
  PRIMARY KEY (service_id, deploy_seq, dep_package_id, dep_version_string,
               ref_package_id, ref_version_string),
  FOREIGN KEY (service_id, deploy_seq)
    REFERENCES deployment(service_id, deploy_seq) ON DELETE CASCADE,
  FOREIGN KEY (dep_package_id, dep_version_string)
    REFERENCES package_version(package_id, version_string) ON DELETE CASCADE,
  FOREIGN KEY (ref_package_id, ref_version_string)
    REFERENCES package_version(package_id, version_string) ON DELETE CASCADE
);
-- the blast-radius join is by (service_id, deploy_seq): the primary key's prefix serves it; these
-- two serve the package_version cascades
CREATE INDEX idx_deployment_edge_dep ON deployment_edge (dep_package_id, dep_version_string);
CREATE INDEX idx_deployment_edge_ref ON deployment_edge (ref_package_id, ref_version_string);

-- Same rule as the other deployment-hanging tables (010): visible through the parent's policy.
ALTER TABLE deployment_edge ENABLE ROW LEVEL SECURITY;
GRANT SELECT ON deployment_edge TO authenticated;
CREATE POLICY deployment_edge_select_authenticated ON deployment_edge FOR SELECT TO authenticated
  USING (service_id IN (SELECT service_id FROM service));
