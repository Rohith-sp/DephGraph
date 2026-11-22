-- Maintainer activity + abandonment score based on it (001-006 are immutable).
--
-- Synopsis: "Maintainer Abandonment Scoring: computes a risk score per package based on maintainer
-- inactivity and single-maintainer exposure" (xz-utils style). The course MySQL schema already does
-- this in er-diagram/schema.sql, view v_package_abandonment; this migration brings the app in line.
--
-- maintainer.last_active_at is filled by POST /internal/maintainers-sync
-- (app/services/maintainers.py) from the public npm / PyPI / crates.io registries.
-- NULL = unknown (never synced, Go module, or the registry gave no date).

ALTER TABLE maintainer ADD COLUMN IF NOT EXISTS last_active_at TIMESTAMPTZ;

-- v_abandonment keeps the 002 contract (name, version_string, ecosystem, abandonment_score INT 0-100,
-- HIGHER = MORE abandoned = WORSE) because pr_gate_engine._signals reads it (>= 40 ADVISORY,
-- >= 80 BLOCK). Same column names/order/types, so CREATE OR REPLACE works.
--
-- A) Package HAS maintainer activity data (at least one maintainer with last_active_at):
--    same formula as MySQL v_package_abandonment:
--        (50 if maintainer_count <= 1 else 0)            -- single-maintainer exposure
--      + LEAST(days since MAX(last_active_at), 730) / 14.6   -- inactivity, 0..50 over 2 years
--    Examples: 1 maintainer idle 2+ years -> 50 + 50 = 100 (BLOCK);
--              3 maintainers, last active 30 days ago -> 0 + 30/14.6 = 2 (PASS).
--
-- B) Fallback, NO maintainer activity data (no maintained_by rows, or none of the maintainers has a
--    last_active_at yet): the original 002 version-level proxy, unchanged:
--        publish age in years * 15, minus 8 per known maintainer (50 if published_at is unknown).
--    Why: before the first maintainers-sync (and for Go, which has no registry data) we cannot tell
--    "abandoned" from "not synced yet". Treating missing data as 0 maintainers would put every such
--    package at >= 50 (ADVISORY) through the single-maintainer term, and a fixed neutral value would
--    throw away the publish-age signal we do have. Keeping the old formula means un-synced packages
--    score exactly as before (seed data and PR-gate behaviour unchanged) and a fresh release still
--    scores 0.
--
-- Both branches: +40 if the version is yanked. This is an app-only, version-level signal (the MySQL
-- view is per package and has no yank flag); a yanked release is a strong "do not adopt" signal.
-- Result is clamped to 0..100 and cast to INT (rounds).
CREATE OR REPLACE VIEW v_abandonment WITH (security_invoker = true) AS
WITH maint AS (
  SELECT mb.package_id,
         COUNT(*)              AS maintainer_count,
         MAX(m.last_active_at) AS last_active_at   -- MAX ignores NULLs, like MySQL
  FROM maintained_by mb
  JOIN maintainer m ON m.maintainer_id = mb.maintainer_id
  GROUP BY mb.package_id
)
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
LEFT JOIN maint mt      ON mt.package_id = p.package_id;

-- CREATE OR REPLACE VIEW keeps existing privileges, so the 004 grant survives; repeated here so the
-- view is readable even if 004 is ever applied in a different order. Idempotent.
GRANT SELECT ON v_abandonment TO authenticated;
