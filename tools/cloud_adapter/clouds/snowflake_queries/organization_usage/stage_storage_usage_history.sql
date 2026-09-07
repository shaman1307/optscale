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
    u.usage_date,
    u.average_stage_bytes,
    r.effective_rate AS effective_rate,
    r.currency AS currency
FROM SNOWFLAKE.ORGANIZATION_USAGE.STAGE_STORAGE_USAGE_HISTORY u
LEFT JOIN rates r
  ON r.date = u.usage_date
 AND r.account_locator = u.account_locator
 AND r.region = u.region
 AND r.service_type = 'STORAGE'
WHERE u.usage_date >= %(start)s
  AND u.usage_date < %(end)s
ORDER BY u.usage_date
