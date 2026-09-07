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
    u.warehouse_id,
    u.warehouse_name,
    u.start_time,
    u.end_time,
    u.credits_used,
    u.credits_used_compute,
    u.credits_used_cloud_services,
    r_wh.effective_rate AS effective_rate,
    r_wh.currency AS currency,
    r_cs.effective_rate AS cloud_services_effective_rate
FROM SNOWFLAKE.ORGANIZATION_USAGE.WAREHOUSE_METERING_HISTORY u
LEFT JOIN rates r_wh
  ON r_wh.date = DATE(u.start_time)
 AND r_wh.account_locator = u.account_locator
 AND r_wh.region = u.region
 AND r_wh.service_type = u.service_type
LEFT JOIN rates r_cs
  ON r_cs.date = DATE(u.start_time)
 AND r_cs.account_locator = u.account_locator
 AND r_cs.region = u.region
 AND r_cs.service_type = 'CLOUD_SERVICES'
WHERE u.start_time >= %(start)s
  AND u.start_time < %(end)s
ORDER BY u.start_time
