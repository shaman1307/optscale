SELECT
    start_time,
    end_time,
    function_name,
    model_name,
    query_id,
    warehouse_id,
    user_id,
    role_names,
    query_tag,
    metrics,
    credits,
    is_completed
FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_AI_FUNCTIONS_USAGE_HISTORY
WHERE start_time >= %s
  AND start_time < %s
ORDER BY start_time
