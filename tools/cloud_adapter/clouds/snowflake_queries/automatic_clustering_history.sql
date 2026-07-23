SELECT
    DATE_TRUNC('DAY', start_time) AS start_time,
    DATE_TRUNC('DAY', start_time) AS end_time,
    SUM(credits_used) AS credits_used,
    SUM(num_bytes_reclustered) AS num_bytes_reclustered,
    SUM(num_rows_reclustered) AS num_rows_reclustered,
    table_id,
    ANY_VALUE(table_name) AS table_name,
    ANY_VALUE(schema_id) AS schema_id,
    ANY_VALUE(schema_name) AS schema_name,
    ANY_VALUE(database_id) AS database_id,
    ANY_VALUE(database_name) AS database_name
FROM SNOWFLAKE.ACCOUNT_USAGE.AUTOMATIC_CLUSTERING_HISTORY
WHERE start_time >= %s
  AND start_time < %s
GROUP BY DATE_TRUNC('DAY', start_time), table_id
ORDER BY start_time
