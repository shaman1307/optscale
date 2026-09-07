-- ORGANIZATION_USAGE AUTOMATIC_CLUSTERING_HISTORY is already daily per table.
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
    u.usage_date AS start_time,
    u.usage_date AS end_time,
    u.credits_used,
    u.num_bytes_reclustered,
    u.num_rows_reclustered,
    u.table_id,
    u.table_name,
    u.schema_id,
    u.schema_name,
    u.database_id,
    u.database_name,
    r.effective_rate AS effective_rate,
    r.currency AS currency
FROM SNOWFLAKE.ORGANIZATION_USAGE.AUTOMATIC_CLUSTERING_HISTORY u
LEFT JOIN rates r
  ON r.date = u.usage_date
 AND r.account_locator = u.account_locator
 AND r.region = u.region
 AND r.service_type = 'AUTOMATIC_CLUSTERING'
WHERE u.usage_date >= %(start)s
  AND u.usage_date < %(end)s
ORDER BY u.usage_date
