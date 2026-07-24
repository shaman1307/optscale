SELECT
    COALESCE(consumer_account_locator, provider_account_locator) AS account_locator,
    usage_date,
    report_date,
    provider_name,
    provider_account_name,
    provider_organization_name,
    listing_display_name,
    listing_global_name,
    database_name,
    charge_type,
    units,
    unit_price,
    charge,
    currency
FROM SNOWFLAKE.ORGANIZATION_USAGE.MARKETPLACE_PAID_USAGE_DAILY
WHERE usage_date >= %s
  AND usage_date < %s
ORDER BY usage_date
