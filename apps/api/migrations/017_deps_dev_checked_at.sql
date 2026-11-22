-- deps_dev_checked_at: when deps.dev last answered for this exact package version (001-016 are
--   immutable). A version whose license / publish date deps.dev lacks is only re-asked after the
--   gate's OSV max-age TTL, not on every PR.
ALTER TABLE package_version ADD COLUMN IF NOT EXISTS deps_dev_checked_at TIMESTAMPTZ;
