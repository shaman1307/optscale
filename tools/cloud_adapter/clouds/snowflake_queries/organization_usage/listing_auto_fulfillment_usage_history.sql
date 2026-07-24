-- Cross-region listing auto-fulfillment (replication / transfer / storage)
-- billed at organization level — not present in ACCOUNT_USAGE.
SELECT
    account_locator,
    account_name,
    organization_name,
    region,
    usage_date,
    service_type,
    currency,
    estimated_usage,
    estimated_usage_in_currency,
    provider_account_region,
    provider_account_name,
    provider_account_locator
FROM SNOWFLAKE.ORGANIZATION_USAGE.LISTING_AUTO_FULFILLMENT_USAGE_HISTORY
WHERE usage_date >= %s
  AND usage_date < %s
ORDER BY usage_date
