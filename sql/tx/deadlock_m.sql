-- Deadlock, monitor session M: at t = 2.5 s A holds X and waits for Y,
-- B holds Y. At t = 3 s B asks for X and closes the cycle.
DO SLEEP(GREATEST(0, @t0 + 2.5 - UNIX_TIMESTAMP(NOW(6))));
SELECT CONVERT(u.VARIABLE_VALUE USING utf8mb4) AS session,
       l.OBJECT_NAME AS table_name, l.LOCK_TYPE AS lock_type, l.LOCK_MODE AS lock_mode,
       l.LOCK_STATUS AS lock_status, l.LOCK_DATA AS lock_data
FROM   performance_schema.data_locks l
LEFT JOIN performance_schema.user_variables_by_thread u
       ON u.THREAD_ID = l.THREAD_ID AND u.VARIABLE_NAME = 'session_label'
WHERE  l.OBJECT_SCHEMA = DATABASE() AND l.LOCK_TYPE = 'RECORD'
ORDER  BY session, l.OBJECT_NAME;
