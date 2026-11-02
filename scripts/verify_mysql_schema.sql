-- Checks that schema.sql created the full DephGraph schema.
-- Expected: 23 tables, 3 views, 26 foreign keys.

USE dephgraph;

SELECT
  (SELECT COUNT(*) FROM information_schema.tables
    WHERE table_schema = 'dephgraph' AND table_type = 'BASE TABLE')       AS tables_found,
  (SELECT COUNT(*) FROM information_schema.views
    WHERE table_schema = 'dephgraph')                                     AS views_found,
  (SELECT COUNT(*) FROM information_schema.referential_constraints
    WHERE constraint_schema = 'dephgraph')                                AS foreign_keys_found,
  (SELECT COUNT(*) FROM information_schema.check_constraints
    WHERE constraint_schema = 'dephgraph')                                AS check_constraints_found;

SELECT t.table_name,
       (SELECT COUNT(*) FROM information_schema.columns c
         WHERE c.table_schema = t.table_schema AND c.table_name = t.table_name) AS columns,
       (SELECT GROUP_CONCAT(k.column_name ORDER BY k.ordinal_position)
          FROM information_schema.key_column_usage k
         WHERE k.table_schema = t.table_schema AND k.table_name = t.table_name
           AND k.constraint_name = 'PRIMARY')                                   AS primary_key
FROM information_schema.tables t
WHERE t.table_schema = 'dephgraph' AND t.table_type = 'BASE TABLE'
ORDER BY t.table_name;
