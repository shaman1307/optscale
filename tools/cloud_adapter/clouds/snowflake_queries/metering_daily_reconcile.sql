SELECT
    service_type,
    usage_date,
    credits_used_compute,
    credits_used_cloud_services,
    credits_billed
FROM SNOWFLAKE.ACCOUNT_USAGE.METERING_DAILY_HISTORY
WHERE usage_date >= %s
  AND usage_date < %s
  AND service_type IN (
      'WAREHOUSE_METERING',
      'PIPE',
      'SNOWPIPE',
      'AUTO_CLUSTERING'
  )
ORDER BY usage_date, service_type
