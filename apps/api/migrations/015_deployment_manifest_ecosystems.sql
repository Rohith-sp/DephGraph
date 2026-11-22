-- Which ecosystems of a deployment came from plain manifests (001-014 are immutable).
--
-- A deployment can mix a lockfile ecosystem (exact edges in deployment_edge) with a manifest
-- ecosystem (edges from the global depends_on catalogue). The graph must take depends_on edges
-- only for the latter, otherwise other projects' resolutions leak into a locked ecosystem.
-- `locked` stays (immutable history) but readers use this column: it is the ecosystems whose
-- edges come from depends_on. Existing unlocked deployments were all manifest-based.
ALTER TABLE deployment ADD COLUMN manifest_ecosystems TEXT[] NOT NULL DEFAULT '{}';

UPDATE deployment d SET manifest_ecosystems = COALESCE((
  SELECT array_agg(DISTINCT p.ecosystem) FROM resolves_to r
  JOIN package p ON p.package_id = r.package_id
  WHERE r.service_id = d.service_id AND r.deploy_seq = d.deploy_seq), '{}')
WHERE NOT d.locked;

-- 013's system tables: API only, like 012's (guarded: plain Postgres has no anon / authenticated)
DO $$ DECLARE r TEXT; BEGIN
  FOR r IN SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated') LOOP
    EXECUTE format('REVOKE ALL ON alert_issue, alert_skip, service_import FROM %I', r);
  END LOOP;
END $$;
