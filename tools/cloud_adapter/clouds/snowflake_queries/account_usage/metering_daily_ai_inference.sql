-- Reconciliation helper only — do not import as a primary collector
-- (would double-count with per-request Cortex views).
SELECT
    usage_date,
    service_type,
    credits_used_compute,
    credits_used_cloud_services,
    credits_used,
    credits_adjustment_cloud_services,
    credits_billed
FROM SNOWFLAKE.ACCOUNT_USAGE.METERING_DAILY_HISTORY
WHERE service_type = 'AI_INFERENCE'
  AND usage_date >= %s
  AND usage_date < %s
ORDER BY usage_date
