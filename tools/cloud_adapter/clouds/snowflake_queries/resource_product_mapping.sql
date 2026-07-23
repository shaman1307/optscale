SELECT
    resource_name,
    resource_type,
    product
FROM SNOWFLAKE_PROD.BUDGET.RESOURCE_PRODUCT_MAPPING
WHERE account_locator = %s
  AND yearquarter = %s
