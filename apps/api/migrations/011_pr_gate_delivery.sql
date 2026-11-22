-- Durable PR-gate webhook deliveries (001-010 are immutable).
--
-- Every accepted pull_request delivery is stored before it is reviewed, keyed by GitHub's delivery
-- id, so a redelivery is a no-op and a crashed or failed review is retried by the cron sweeper
-- (POST /internal/pr-gate/retry). 'processing' rows carry a lease in next_attempt_at: when it
-- passes, the worker is presumed dead. System table: RLS on, no policy, no grant to authenticated.
CREATE TABLE pr_gate_delivery (
  delivery_id     TEXT        PRIMARY KEY,
  event           TEXT        NOT NULL,
  payload         JSONB       NOT NULL,
  status          TEXT        NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'processing', 'done', 'failed')),
  attempts        INT         NOT NULL DEFAULT 0,
  last_error      TEXT,
  received_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_pr_gate_delivery_due ON pr_gate_delivery (next_attempt_at)
  WHERE status IN ('pending', 'processing');
ALTER TABLE pr_gate_delivery ENABLE ROW LEVEL SECURITY;
