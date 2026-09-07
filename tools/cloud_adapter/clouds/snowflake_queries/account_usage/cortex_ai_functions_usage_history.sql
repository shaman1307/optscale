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
    u.start_time,
    u.end_time,
    u.function_name,
    u.model_name,
    u.query_id,
    u.warehouse_id,
    u.user_id,
    u.role_names,
    u.query_tag,
    u.metrics,
    u.credits,
    u.is_completed,
    r.effective_rate AS effective_rate,
    r.currency AS currency
FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_AI_FUNCTIONS_USAGE_HISTORY u
LEFT JOIN rates r
  ON r.date = DATE(u.start_time)
 AND r.account_locator = CURRENT_ACCOUNT()
 AND r.service_type = 'AI_FUNCTIONS'
WHERE u.start_time >= %(start)s
  AND u.start_time < %(end)s
ORDER BY u.start_time
