SELECT
    usage_date,
    average_stage_bytes
FROM SNOWFLAKE.ACCOUNT_USAGE.STAGE_STORAGE_USAGE_HISTORY
WHERE usage_date >= %s
  AND usage_date < %s
ORDER BY usage_date
