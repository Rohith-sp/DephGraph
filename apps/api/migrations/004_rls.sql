-- Supabase ships this role; create it on plain Postgres (local dev / CI).
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
    CREATE ROLE authenticated NOLOGIN;
  END IF;
END $$;

GRANT USAGE ON SCHEMA public TO authenticated;

-- RLS on every public table + read-only policy for signed-in users.
-- Writes go through the API (table owner / service role bypasses RLS).
DO $$ DECLARE t TEXT; BEGIN
  FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('GRANT SELECT ON %I TO authenticated', t);
    EXECUTE format('CREATE POLICY %I ON %I FOR SELECT TO authenticated USING (true)', t || '_select_authenticated', t);
  END LOOP;
END $$;

GRANT SELECT ON v_blast_radius, v_prunable, v_abandonment, v_copyleft_risk, v_cluster_redundancy TO authenticated;
