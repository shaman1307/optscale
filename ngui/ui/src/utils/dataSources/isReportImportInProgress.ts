import { getCurrentUTCTimeInSec } from "utils/datetime";

// Must match REST API thresholds in controllers/report_import.py.
const ACTIVE_IMPORT_THRESHOLD_SECONDS = 30 * 60;
const SCHEDULED_IMPORT_THRESHOLD_SECONDS = 3 * 60 * 60;

type ReportImport = {
  state?: string;
  created_at?: number | null;
  updated_at?: number | null;
};

/**
 * Returns true when this data source still has unfinished import work that
 * should block another billing reimport (fresh scheduled or live in_progress).
 * Mirrors check_unprocessed_imports after fail_stale_imports.
 */
export const isReportImportInProgress = (reportImports: ReportImport[] = []) => {
  const now = getCurrentUTCTimeInSec();
  const activeThreshold = now - ACTIVE_IMPORT_THRESHOLD_SECONDS;
  const scheduledThreshold = now - SCHEDULED_IMPORT_THRESHOLD_SECONDS;

  return reportImports.some((reportImport) => {
    if (reportImport.state === "in_progress") {
      return (reportImport.updated_at ?? 0) >= activeThreshold;
    }
    if (reportImport.state === "scheduled") {
      return (reportImport.created_at ?? 0) >= scheduledThreshold;
    }
    return false;
  });
};
