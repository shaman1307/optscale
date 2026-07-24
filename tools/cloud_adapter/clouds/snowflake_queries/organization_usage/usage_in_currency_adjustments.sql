-- Organization-level discounts, rebates and bill adjustments (currency).
-- These are missing from ACCOUNT_USAGE detail views.
SELECT
    account_locator,
    account_name,
    organization_name,
    region,
    usage_date,
    usage_type,
    service_type,
    usage,
    currency,
    usage_in_currency,
    balance_source,
    billing_type,
    rating_type,
    is_adjustment
FROM SNOWFLAKE.ORGANIZATION_USAGE.USAGE_IN_CURRENCY_DAILY
WHERE usage_date >= %s
  AND usage_date < %s
  AND is_adjustment = TRUE
ORDER BY usage_date
