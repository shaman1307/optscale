SELECT
    start_time,
    end_time,
    source_cloud,
    source_region,
    target_cloud,
    target_region,
    bytes_transferred,
    transfer_type
FROM SNOWFLAKE.ACCOUNT_USAGE.DATA_TRANSFER_HISTORY
WHERE start_time >= %s
  AND start_time < %s
ORDER BY start_time
