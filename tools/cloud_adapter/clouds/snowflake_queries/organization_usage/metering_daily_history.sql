-- Remaining service types without a dedicated collector (COMPUTE / AI_COMPUTE).
-- Dedicated WH / pipe / clustering / storage stay on their own collectors.
-- REPLICATION credits on auto-fulfillment accounts are the same usage as
-- LISTING_AUTO_FULFILLMENT_USAGE_HISTORY (service_type=REPLICATION) and are
-- billed there via estimated_usage_in_currency — importing both double-counts.
WITH rates AS (
    SELECT
        date,
        account_locator,
        region,
        service_type,
        rating_type,
        effective_rate,
        currency
    FROM SNOWFLAKE.ORGANIZATION_USAGE.RATE_SHEET_DAILY
    WHERE UPPER(billing_type) = 'CONSUMPTION'
      AND is_adjustment = FALSE
      AND date >= DATE(%(start)s)
      AND date < DATE(%(end)s)
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY date, account_locator, region, service_type
        ORDER BY
            CASE UPPER(rating_type)
                WHEN 'COMPUTE' THEN 0
                WHEN 'AI_COMPUTE' THEN 0
                WHEN 'STORAGE' THEN 0
                ELSE 1
            END,
            contract_number DESC NULLS LAST
    ) = 1
)
SELECT
    u.account_locator,
    u.account_name,
    u.organization_name,
    u.region,
    u.service_type,
    u.usage_date,
    u.credits_used_compute,
    u.credits_used_cloud_services,
    u.credits_adjustment_cloud_services,
    u.credits_billed,
    r.effective_rate AS effective_rate,
    r.currency AS currency
FROM SNOWFLAKE.ORGANIZATION_USAGE.METERING_DAILY_HISTORY u
LEFT JOIN rates r
  ON r.date = u.usage_date
 AND r.account_locator = u.account_locator
 AND r.region = u.region
 AND r.service_type = CASE u.service_type
      WHEN 'PIPE' THEN 'SNOWPIPE'
      WHEN 'AUTO_CLUSTERING' THEN 'AUTOMATIC_CLUSTERING'
      ELSE u.service_type
    END
WHERE u.usage_date >= %(start)s
  AND u.usage_date < %(end)s
  AND u.service_type NOT IN (
      'WAREHOUSE_METERING',
      'PIPE',
      'SNOWPIPE',
      'AUTO_CLUSTERING',
      'DATABASE_STORAGE',
      'STAGE',
      'REPLICATION'
  )
ORDER BY u.usage_date
