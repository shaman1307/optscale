SELECT
    h.pipe_id,
    h.pipe_name,
    p.pipe_catalog,
    p.pipe_schema,
    h.start_time,
    h.end_time,
    h.credits_used,
    h.bytes_inserted,
    h.files_inserted
FROM SNOWFLAKE.ACCOUNT_USAGE.PIPE_USAGE_HISTORY AS h
LEFT JOIN SNOWFLAKE.ACCOUNT_USAGE.PIPES AS p
    ON h.pipe_id = p.pipe_id
WHERE h.start_time >= %s
  AND h.start_time < %s
ORDER BY h.start_time
