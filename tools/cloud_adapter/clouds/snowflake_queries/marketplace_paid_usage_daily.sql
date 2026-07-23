SELECT
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
FROM SNOWFLAKE.DATA_SHARING_USAGE.MARKETPLACE_PAID_USAGE_DAILY
WHERE usage_date >= %s
  AND usage_date < %s
ORDER BY usage_date
