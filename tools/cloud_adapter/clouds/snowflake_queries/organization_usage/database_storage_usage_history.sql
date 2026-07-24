SELECT
    account_locator,
    account_name,
    organization_name,
    region,
    database_id,
    database_name,
    usage_date,
    average_database_bytes,
    average_failsafe_bytes
FROM SNOWFLAKE.ORGANIZATION_USAGE.DATABASE_STORAGE_USAGE_HISTORY
WHERE usage_date >= %s
  AND usage_date < %s
ORDER BY usage_date
