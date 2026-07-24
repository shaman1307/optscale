-- Remaining serverless / platform service types without a dedicated collector.
-- Do NOT import WAREHOUSE / PIPE / storage / AI_SERVICES aggregates here —
-- warehouse/storage have dedicated collectors; literal AI_SERVICES in daily is
-- usually empty while Cortex spend appears as SNOWFLAKE_COCO_* / AI_FUNCTIONS
-- (kept here so ORGANIZATION_USAGE carries org AI billing).
SELECT
    account_locator,
    account_name,
    organization_name,
    region,
    service_type,
    usage_date,
    credits_used_compute,
    credits_used_cloud_services,
    credits_adjustment_cloud_services,
    credits_billed
FROM SNOWFLAKE.ORGANIZATION_USAGE.METERING_DAILY_HISTORY
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
