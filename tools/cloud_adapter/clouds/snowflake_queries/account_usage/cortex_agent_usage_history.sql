SELECT
    start_time,
    end_time,
    user_id,
    user_name,
    request_id,
    parent_request_id,
    agent_database_id,
    agent_database_name,
    agent_schema_id,
    agent_schema_name,
    agent_id,
    agent_name,
    agent_tags,
    token_credits,
    tokens,
    tokens_granular,
    credits_granular,
    metadata
FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_AGENT_USAGE_HISTORY
WHERE start_time >= %s
  AND start_time < %s
ORDER BY start_time
