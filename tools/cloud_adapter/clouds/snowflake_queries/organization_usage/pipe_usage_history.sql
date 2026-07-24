-- ORGANIZATION_USAGE is already daily (USAGE_DATE); no PIPES join available.
SELECT
    account_locator,
    account_name,
    organization_name,
    region,
    pipe_id,
    pipe_name,
    usage_date AS start_time,
    usage_date AS end_time,
    credits_used,
    bytes_inserted,
    files_inserted,
    CAST(NULL AS VARCHAR) AS pipe_catalog,
    CAST(NULL AS VARCHAR) AS pipe_schema
FROM SNOWFLAKE.ORGANIZATION_USAGE.PIPE_USAGE_HISTORY
WHERE usage_date >= %s
  AND usage_date < %s
ORDER BY usage_date
