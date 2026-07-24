SELECT
    event_date,
    exchange_name,
    snowflake_region,
    listing_name,
    listing_display_name,
    listing_global_name,
    share_name,
    consumer_account_locator,
    consumer_account_name,
    consumer_organization,
    consumer_name,
    jobs,
    unique_users_1d,
    region_group
FROM SNOWFLAKE.DATA_SHARING_USAGE.LISTING_CONSUMPTION_DAILY
WHERE event_date >= %s
  AND event_date < %s
ORDER BY event_date
