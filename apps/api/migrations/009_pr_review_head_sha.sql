-- PR gate: overlapping webhook deliveries must not let an older head overwrite newer verdicts
-- (001-008 are immutable).
--
-- head_sha     = pull_request.head.sha the stored verdicts were computed for.
-- evaluated_at = pull_request.updated_at of that delivery (event time, NOT wall clock: GitHub bumps
--                it on every push, so it orders deliveries even when background jobs finish out of
--                order). store_verdicts only replaces a review whose evaluated_at is not newer.
-- NULL on rows written before this migration = always replaceable.
ALTER TABLE pr_review ADD COLUMN IF NOT EXISTS head_sha VARCHAR(64);
ALTER TABLE pr_review ADD COLUMN IF NOT EXISTS evaluated_at TIMESTAMPTZ;
