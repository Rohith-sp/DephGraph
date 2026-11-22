-- Self-serve onboarding (001-011 are immutable).
--
-- team_invite: pending invitations by email (valid 7 days), claimed into team_member when the
-- invited person signs in with that confirmed email. team_member.email: shown in member lists.
-- service.root_path: the monorepo subdirectory a service covers, so one repo can back several
-- services; (repo_url, root_path) is unique across ALL teams, because the PR gate routes a webhook
-- to every service with the repository's URL and a second owner would receive the first one's data.
-- service_token: ingest credentials for runtime collectors; only the SHA-256 of the token is
-- stored. team_invite, service_token and pr_gate_delivery are API-only (RLS on, no policy, no
-- grants to anon/authenticated): invites hold email addresses, tokens hold credentials.
CREATE TABLE team_invite (
  team_id    BIGINT      NOT NULL REFERENCES team(team_id) ON DELETE CASCADE,
  email      TEXT        NOT NULL CHECK (email = lower(email)),
  role       TEXT        NOT NULL DEFAULT 'member' CHECK (role IN ('member', 'admin')),
  invited_by UUID        NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL DEFAULT now() + interval '7 days',
  PRIMARY KEY (team_id, email)
);
CREATE INDEX idx_team_invite_email ON team_invite(email);

ALTER TABLE team_member ADD COLUMN email TEXT;

ALTER TABLE service ADD COLUMN IF NOT EXISTS root_path TEXT NOT NULL DEFAULT '';
-- Filled when the GitHub App's access to the repository is verified (NULL while the App is not configured).
ALTER TABLE service ADD COLUMN github_installation_id BIGINT, ADD COLUMN github_repo_id BIGINT;
-- Same normal form as the PR gate (routers/pr_gate.py normalize_repo_url): lowercase, https, no
-- trailing '/' or '.git'.
UPDATE service
SET repo_url = regexp_replace(regexp_replace(lower(btrim(repo_url)), '^http://', 'https://'),
                              '(\.git)?/*$', '')
WHERE repo_url IS NOT NULL;
DO $$ DECLARE dup TEXT; BEGIN
  SELECT string_agg(DISTINCT repo_url, ', ') INTO dup FROM (
    SELECT repo_url FROM service WHERE repo_url IS NOT NULL
    GROUP BY repo_url, root_path HAVING count(*) > 1) d;
  IF dup IS NOT NULL THEN
    RAISE EXCEPTION 'migration 012: repository registered more than once (merge or delete the duplicate services first): %', dup;
  END IF;
END $$;
ALTER TABLE service ADD CONSTRAINT service_repo_root_key UNIQUE (repo_url, root_path);
CREATE UNIQUE INDEX service_github_repo_root_key ON service (github_repo_id, root_path) WHERE github_repo_id IS NOT NULL;

CREATE TABLE service_token (
  token_id     BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  service_id   BIGINT      NOT NULL REFERENCES service(service_id) ON DELETE CASCADE,
  token_hash   TEXT        NOT NULL UNIQUE,
  name         TEXT        NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at TIMESTAMPTZ,
  revoked_at   TIMESTAMPTZ
);
CREATE INDEX idx_service_token_service ON service_token(service_id);

ALTER TABLE team_invite ENABLE ROW LEVEL SECURITY;
ALTER TABLE service_token ENABLE ROW LEVEL SECURITY;

-- Supabase's default privileges grant new tables to anon/authenticated (PostgREST): take them back.
DO $$ DECLARE r TEXT; BEGIN
  FOR r IN SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated') LOOP
    EXECUTE format('REVOKE ALL ON team_invite, service_token, pr_gate_delivery FROM %I', r);
  END LOOP;
END $$;
