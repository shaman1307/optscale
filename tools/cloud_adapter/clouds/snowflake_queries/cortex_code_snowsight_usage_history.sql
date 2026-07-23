SELECT
    usage_time,
    user_id,
    user_name,
    request_id,
    parent_request_id,
    token_credits,
    tokens,
    tokens_granular,
    credits_granular,
    metadata
FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_CODE_SNOWSIGHT_USAGE_HISTORY
WHERE usage_time >= %s
  AND usage_time < %s
ORDER BY usage_time
