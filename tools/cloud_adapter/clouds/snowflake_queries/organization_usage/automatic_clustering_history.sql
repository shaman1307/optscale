-- ORGANIZATION_USAGE AUTOMATIC_CLUSTERING_HISTORY is already daily per table.
SELECT
    account_locator,
    account_name,
    organization_name,
    region,
    usage_date AS start_time,
    usage_date AS end_time,
    credits_used,
    num_bytes_reclustered,
    num_rows_reclustered,
    table_id,
    table_name,
    schema_id,
    schema_name,
    database_id,
    database_name
FROM SNOWFLAKE.ORGANIZATION_USAGE.AUTOMATIC_CLUSTERING_HISTORY
WHERE usage_date >= %s
  AND usage_date < %s
ORDER BY usage_date
