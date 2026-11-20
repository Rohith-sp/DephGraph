-- Lock wait, monitor session M: at t = 3 s (A holds the lock, B waits)
-- read the lock tables of performance_schema.
DO SLEEP(GREATEST(0, @t0 + 3.0 - UNIX_TIMESTAMP(NOW(6))));
SELECT CONVERT(u.VARIABLE_VALUE USING utf8mb4) AS session,
       l.OBJECT_NAME AS table_name, l.INDEX_NAME AS index_name,
       l.LOCK_TYPE AS lock_type, l.LOCK_MODE AS lock_mode,
       l.LOCK_STATUS AS lock_status, l.LOCK_DATA AS lock_data
FROM   performance_schema.data_locks l
LEFT JOIN performance_schema.user_variables_by_thread u
       ON u.THREAD_ID = l.THREAD_ID AND u.VARIABLE_NAME = 'session_label'
WHERE  l.OBJECT_SCHEMA = DATABASE()
ORDER  BY session, l.LOCK_TYPE DESC, l.OBJECT_NAME, l.LOCK_STATUS;
SELECT CONVERT(ur.VARIABLE_VALUE USING utf8mb4) AS waiting_session,
       CONVERT(ub.VARIABLE_VALUE USING utf8mb4) AS blocking_session,
       rl.OBJECT_NAME AS table_name,
       rl.LOCK_MODE   AS wanted_mode,
       bl.LOCK_MODE   AS held_mode,
       rl.LOCK_DATA   AS row_key
FROM   performance_schema.data_lock_waits w
JOIN   performance_schema.data_locks rl ON rl.ENGINE_LOCK_ID = w.REQUESTING_ENGINE_LOCK_ID
JOIN   performance_schema.data_locks bl ON bl.ENGINE_LOCK_ID = w.BLOCKING_ENGINE_LOCK_ID
LEFT JOIN performance_schema.user_variables_by_thread ur
       ON ur.THREAD_ID = w.REQUESTING_THREAD_ID AND ur.VARIABLE_NAME = 'session_label'
LEFT JOIN performance_schema.user_variables_by_thread ub
       ON ub.THREAD_ID = w.BLOCKING_THREAD_ID AND ub.VARIABLE_NAME = 'session_label'
WHERE  rl.OBJECT_SCHEMA = DATABASE();
SELECT wait_age_secs, locked_table, waiting_query
FROM   sys.innodb_lock_waits
WHERE  locked_table LIKE CONCAT('`', DATABASE(), '`.%');
