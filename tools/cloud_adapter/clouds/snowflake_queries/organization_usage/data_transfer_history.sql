-- Daily aggregate (USAGE_DATE) across accounts in the organization.
SELECT
    account_locator,
    account_name,
    organization_name,
    region,
    usage_date AS start_time,
    usage_date AS end_time,
    source_cloud,
    source_region,
    target_cloud,
    target_region,
    bytes_transferred,
    transfer_type
FROM SNOWFLAKE.ORGANIZATION_USAGE.DATA_TRANSFER_HISTORY
WHERE usage_date >= %s
  AND usage_date < %s
ORDER BY usage_date
