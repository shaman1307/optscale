SELECT
    account_locator,
    account_name,
    organization_name,
    region,
    warehouse_id,
    warehouse_name,
    start_time,
    end_time,
    credits_used,
    credits_used_compute,
    credits_used_cloud_services
FROM SNOWFLAKE.ORGANIZATION_USAGE.WAREHOUSE_METERING_HISTORY
WHERE start_time >= %s
  AND start_time < %s
ORDER BY start_time
