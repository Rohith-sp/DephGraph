-- Real-data bookkeeping (001-015 are immutable).
--
-- osv_checked_at: when OSV was last queried for this exact package version (set by every OSV
--   fetch: ingest, scan, osv-sync, PR gate). NULL = never checked. The PR gate treats NULL or an
--   old value as "not verified" and asks OSV live instead of trusting an absent `affects` row.
-- package.registry_synced_at: last maintainers-sync attempt for the package (descriptions,
--   maintainers and activity come from the same registry call); rotates the bounded sync batch.
-- deployment.closure_truncated: the transitive closure of a manifest-sourced deployment stopped
--   at the depth cap while more dependencies existed, so the graph and blast radius are partial.
ALTER TABLE package_version ADD COLUMN IF NOT EXISTS osv_checked_at TIMESTAMPTZ;
ALTER TABLE package ADD COLUMN IF NOT EXISTS registry_synced_at TIMESTAMPTZ;
ALTER TABLE deployment ADD COLUMN IF NOT EXISTS closure_truncated BOOLEAN NOT NULL DEFAULT FALSE;
