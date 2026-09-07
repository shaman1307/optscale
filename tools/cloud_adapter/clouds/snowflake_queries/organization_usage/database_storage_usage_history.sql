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
    u.database_id,
    u.database_name,
    u.usage_date,
    u.average_database_bytes,
    u.average_failsafe_bytes,
    r.effective_rate AS effective_rate,
    r.currency AS currency
FROM SNOWFLAKE.ORGANIZATION_USAGE.DATABASE_STORAGE_USAGE_HISTORY u
LEFT JOIN rates r
  ON r.date = u.usage_date
 AND r.account_locator = u.account_locator
 AND r.region = u.region
 AND r.service_type = 'STORAGE'
WHERE u.usage_date >= %(start)s
  AND u.usage_date < %(end)s
  -- Auto-fulfillment STORAGE is billed in LISTING_AUTO_FULFILLMENT_USAGE_HISTORY
  -- (estimated_usage_in_currency). Same account/day/region average bytes here
  -- would double-count when priced via RATE_SHEET_DAILY.
  AND NOT EXISTS (
      SELECT 1
      FROM SNOWFLAKE.ORGANIZATION_USAGE.LISTING_AUTO_FULFILLMENT_USAGE_HISTORY l
      WHERE l.account_locator = u.account_locator
        AND l.usage_date = u.usage_date
        AND l.region = u.region
        AND l.service_type = 'STORAGE'
  )
ORDER BY u.usage_date
