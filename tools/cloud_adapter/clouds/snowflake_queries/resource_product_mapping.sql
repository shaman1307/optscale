-- Product tags for OptScale resources.
-- Joined in Python by (account_locator, resource_name, resource_type,
-- yearquarter) where yearquarter is taken from each expense billing date
-- (start_date), not from the import/load day.
--
-- Load all account locators: ORGANIZATION_USAGE imports multi-account rows
-- while CURRENT_ACCOUNT() is only the org admin session locator.
--
-- Expenses whose billing quarter has no mapping row simply get no product tag.
SELECT
    account_locator,
    resource_name,
    resource_type,
    yearquarter,
    product
FROM SNOWFLAKE_PROD.BUDGET.RESOURCE_PRODUCT_MAPPING
