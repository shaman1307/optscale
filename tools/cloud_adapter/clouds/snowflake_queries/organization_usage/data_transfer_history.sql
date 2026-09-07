-- Daily aggregate (USAGE_DATE) across accounts in the organization.
-- transfer_type=REPLICATION on auto-fulfillment accounts is the same TiB as
-- LISTING_AUTO_FULFILLMENT_USAGE_HISTORY (service_type='DATA TRANSFER') and is
-- billed there via estimated_usage_in_currency — importing both double-counts
-- once transfer bytes are priced (and duplicates resources at $0 today).
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
  AND transfer_type <> 'REPLICATION'
ORDER BY usage_date
