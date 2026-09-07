SELECT
    account_locator,
    account_name,
    region
FROM SNOWFLAKE.ORGANIZATION_USAGE.ACCOUNTS
WHERE account_locator IS NOT NULL
  AND account_name IS NOT NULL
