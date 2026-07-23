SELECT
    reader_account_name,
    start_time,
    end_time,
    warehouse_id,
    warehouse_name,
    credits_used,
    credits_used_compute,
    credits_used_cloud_services
FROM SNOWFLAKE.READER_ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY
WHERE start_time >= %s
  AND start_time < %s
ORDER BY start_time
