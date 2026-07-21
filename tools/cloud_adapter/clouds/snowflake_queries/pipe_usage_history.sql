SELECT
    pipe_id,
    pipe_name,
    start_time,
    end_time,
    credits_used,
    bytes_inserted,
    files_inserted
FROM SNOWFLAKE.ACCOUNT_USAGE.PIPE_USAGE_HISTORY
WHERE start_time >= %s
  AND start_time < %s
ORDER BY start_time
