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
    u.usage_time,
    u.user_id,
    u.user_name,
    u.request_id,
    u.parent_request_id,
    u.token_credits,
    u.tokens,
    u.tokens_granular,
    u.credits_granular,
    u.metadata,
    r.effective_rate AS effective_rate,
    r.currency AS currency
FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_CODE_SNOWSIGHT_USAGE_HISTORY u
LEFT JOIN rates r
  ON r.date = DATE(u.usage_time)
 AND r.account_locator = CURRENT_ACCOUNT()
 AND r.service_type = 'SNOWFLAKE_COCO_SNOWSIGHT'
WHERE u.usage_time >= %(start)s
  AND u.usage_time < %(end)s
ORDER BY u.usage_time
