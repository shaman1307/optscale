-- ORGANIZATION_USAGE is already daily (USAGE_DATE); no PIPES join available.
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
    u.pipe_id,
    u.pipe_name,
    u.usage_date AS start_time,
    u.usage_date AS end_time,
    u.credits_used,
    u.bytes_inserted,
    u.files_inserted,
    CAST(NULL AS VARCHAR) AS pipe_catalog,
    CAST(NULL AS VARCHAR) AS pipe_schema,
    r.effective_rate AS effective_rate,
    r.currency AS currency
FROM SNOWFLAKE.ORGANIZATION_USAGE.PIPE_USAGE_HISTORY u
LEFT JOIN rates r
  ON r.date = u.usage_date
 AND r.account_locator = u.account_locator
 AND r.region = u.region
 AND r.service_type = 'SNOWPIPE'
WHERE u.usage_date >= %(start)s
  AND u.usage_date < %(end)s
ORDER BY u.usage_date
