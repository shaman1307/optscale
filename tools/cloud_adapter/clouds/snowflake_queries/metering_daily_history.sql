SELECT
    service_type,
    usage_date,
    credits_used_compute,
    credits_used_cloud_services,
    credits_adjustment_cloud_services,
    credits_billed
FROM SNOWFLAKE.ACCOUNT_USAGE.METERING_DAILY_HISTORY
WHERE usage_date >= %s
  AND usage_date < %s
  AND service_type NOT IN (
      'WAREHOUSE_METERING',
      'PIPE',
      'SNOWPIPE',
      'AI_SERVICES',
      'AI_INFERENCE',
      'AUTO_CLUSTERING',
      'DATABASE_STORAGE',
      'STAGE'
  )
  AND service_type NOT LIKE 'CORTEX%%'
ORDER BY usage_date
