import { useEffect, useMemo } from "react";
import CancelIcon from "@mui/icons-material/Cancel";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import SyncIcon from "@mui/icons-material/Sync";
import WarningAmberIcon from "@mui/icons-material/WarningAmber";
import { Box, Typography } from "@mui/material";
import { alpha, useTheme } from "@mui/material/styles";
import { FormattedMessage, FormattedNumber } from "react-intl";
import IconStatus from "components/IconStatus";
import KeyValueLabel from "components/KeyValueLabel/KeyValueLabel";
import SlicedText from "components/SlicedText";
import SubTitle from "components/SubTitle";
import Table from "components/Table";
import TextWithDataTestId from "components/TextWithDataTestId";
import { useReportImportsQuery } from "graphql/__generated__/hooks/restapi";
import { isEmptyArray } from "utils/arrays";
import { hasStageTargetCostMismatch } from "utils/dataSources";
import { EN_FULL_FORMAT_HH_MM_SS, format, formatUTC } from "utils/datetime";
import { CELL_EMPTY_VALUE } from "utils/tables";

const ACTIVE_IMPORT_STATES = new Set(["scheduled", "in_progress"]);
const IMPORT_POLL_MS = 5000;

const CollectorStatus = ({ status }) => {
  if (status === "in_progress") {
    return <IconStatus icon={SyncIcon} color="primary" labelMessageId="inProgress" />;
  }
  if (status === "ok") {
    return <IconStatus icon={CheckCircleIcon} color="success" labelMessageId="completed" />;
  }
  if (status === "skipped") {
    return <IconStatus icon={WarningAmberIcon} color="warning" labelMessageId="skipped" />;
  }
  return <IconStatus icon={CancelIcon} color="error" labelMessageId="failed" />;
};

const ReconcileStatus = ({ status }) => {
  if (status === "ok") {
    return <IconStatus icon={CheckCircleIcon} color="success" labelMessageId="completed" />;
  }
  if (status === "skipped") {
    return <IconStatus icon={WarningAmberIcon} color="warning" labelMessageId="skipped" />;
  }
  return <IconStatus icon={WarningAmberIcon} color="warning" labelMessageId="mismatch" />;
};

const GcpCostValue = ({ value, highlight = false }) => (
  <Typography component="span" variant="body2" color={highlight ? "error" : "inherit"}>
    <FormattedNumber value={value || 0} maximumFractionDigits={2} />
  </Typography>
);

const emptyCollectorRow = (serviceType, status = "in_progress") => ({
  service_type: serviceType,
  records: 0,
  credits: 0,
  average_bytes: 0,
  tb: 0,
  status,
  message: null,
  finished_at: null,
});

/**
 * Child Advanced reuses the tenant report_import.details, but shows only this
 * account_locator's stats. Org-level collectors seed the table (same skeleton
 * as pre-tenant Snowflake) so the grid is visible before per-account rows exist.
 */
const detailsForView = (details, locatorKey, accountLocator) => {
  if (!details) {
    return null;
  }
  if (!locatorKey) {
    return details;
  }
  const orgCollectors = Array.isArray(details.collectors) ? details.collectors : [];
  const accounts = details.accounts;
  let accountCollectors = [];
  if (accounts && typeof accounts === "object") {
    const accountDetails =
      accounts[locatorKey] ||
      accounts[accountLocator] ||
      Object.entries(accounts).find(([key]) => String(key).toUpperCase() === locatorKey)?.[1];
    if (accountDetails && Array.isArray(accountDetails.collectors)) {
      accountCollectors = accountDetails.collectors;
    }
  }
  const byType = new Map(accountCollectors.map((row) => [row.service_type || "UNKNOWN", row]));
  let collectors;
  if (orgCollectors.length > 0) {
    collectors = orgCollectors.map((org) => {
      const key = org.service_type || "UNKNOWN";
      const acc = byType.get(key);
      if (acc) {
        return { ...acc, service_type: key };
      }
      // Keep the service row visible; status follows the org collector until
      // this locator has written rows for that service_type.
      return emptyCollectorRow(key, org.status || "in_progress");
    });
  } else {
    collectors = accountCollectors;
  }
  return {
    ...details,
    collectors,
    // Reconciliation/warnings are org-level; hide on child account view.
    reconciliation: [],
    warnings: [],
  };
};

const BillingImportDetails = ({ dataSourceId, accountLocator = null, variant = "snowflake" }) => {
  const theme = useTheme();
  const { data, loading, startPolling, stopPolling } = useReportImportsQuery({
    variables: {
      cloudAccountId: dataSourceId,
      showCompleted: true,
    },
    skip: !dataSourceId,
  });

  const locatorKey = accountLocator ? String(accountLocator).toUpperCase() : null;

  const { latestDetails, isImportInProgress, overlayInProgress, latestImportAt } = useMemo(() => {
    const imports = data?.reportImports ?? [];
    const isImportInProgress = imports.some((item) => ACTIVE_IMPORT_STATES.has(item.state));
    const activeImport = [...imports]
      .filter((item) => ACTIVE_IMPORT_STATES.has(item.state))
      .sort((a, b) => (b.created_at || 0) - (a.created_at || 0))[0];
    const activeDetails = detailsForView(activeImport?.details, locatorKey, accountLocator);

    const withDetails = imports
      .filter((item) => {
        if (
          (item.state !== "completed" && item.state !== "failed") ||
          !item.details
        ) {
          return false;
        }
        const details = detailsForView(item.details, locatorKey, accountLocator);
        if (!details) {
          return false;
        }
        return (
          (Array.isArray(details.collectors) && details.collectors.length > 0) ||
          (Array.isArray(details.reconciliation) && details.reconciliation.length > 0) ||
          (Array.isArray(details.warnings) && details.warnings.length > 0) ||
          details.source_sum != null ||
          details.local_sum != null ||
          details.target_sum != null
        );
      })
      .sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
    const latestCompletedImport = withDetails[0] || null;
    const completedDetails = latestCompletedImport
      ? detailsForView(latestCompletedImport.details, locatorKey, accountLocator)
      : null;
    const importAt = (item) => item?.updated_at || item?.created_at || null;

    // Prefer the active import as soon as it has seeded collectors (even with
    // zero records for this locator) — same live table as standalone Snowflake.
    if (activeImport && activeDetails) {
      return {
        latestDetails: activeDetails,
        isImportInProgress: true,
        overlayInProgress: false,
        latestImportAt: importAt(activeImport),
      };
    }
    return {
      latestDetails: completedDetails,
      isImportInProgress,
      // A queued import has no collector progress yet. Keep the latest completed
      // results visible until the worker starts processing it.
      overlayInProgress: Boolean(activeImport?.state === "in_progress" && completedDetails),
      latestImportAt: importAt(latestCompletedImport),
    };
  }, [data, locatorKey, accountLocator]);

  useEffect(() => {
    if (isImportInProgress) {
      startPolling(IMPORT_POLL_MS);
    } else {
      stopPolling();
    }
    return () => stopPolling();
  }, [isImportInProgress, startPolling, stopPolling]);

  const collectors = useMemo(() => {
    const rows = latestDetails?.collectors;
    if (!Array.isArray(rows)) {
      return [];
    }
    // Collapse duplicate service_type rows (e.g. multiple AI_SERVICES sources).
    const STATUS_RANK = { ok: 0, skipped: 1, failed: 2, in_progress: 3 };
    const byType = new Map();
    rows.forEach((row) => {
      const key = row.service_type || "UNKNOWN";
      const prev = byType.get(key);
      if (!prev) {
        byType.set(key, {
          service_type: key,
          records: Number(row.records) || 0,
          credits: Number(row.credits) || 0,
          average_bytes: Number(row.average_bytes) || 0,
          tb: Number(row.tb) || 0,
          status: row.status || "ok",
          message: row.message || null,
          finished_at: row.finished_at || null,
        });
        return;
      }
      prev.records += Number(row.records) || 0;
      prev.credits += Number(row.credits) || 0;
      prev.average_bytes += Number(row.average_bytes) || 0;
      prev.tb += Number(row.tb) || 0;
      if ((Number(row.finished_at) || 0) > (Number(prev.finished_at) || 0)) {
        prev.finished_at = row.finished_at;
      }
      const nextRank = STATUS_RANK[row.status] ?? 0;
      const prevRank = STATUS_RANK[prev.status] ?? 0;
      if (nextRank > prevRank) {
        prev.status = row.status;
        prev.message = row.message || prev.message;
      } else if (nextRank === prevRank && !prev.message && row.message) {
        prev.message = row.message;
      }
    });
    return Array.from(byType.values()).map((row) => {
      const base = {
        ...row,
        credits: Math.round(row.credits * 10000) / 10000,
        tb:
          row.average_bytes > 0
            ? Math.round((row.average_bytes / 1024 ** 4) * 10000) / 10000
            : Math.round(row.tb * 10000) / 10000,
      };
      if (overlayInProgress) {
        return {
          ...base,
          status: "in_progress",
          finished_at: null,
          message: null,
        };
      }
      return base;
    });
  }, [latestDetails, overlayInProgress]);

  const reconciliation = useMemo(() => {
    const rows = latestDetails?.reconciliation;
    return Array.isArray(rows) ? rows : [];
  }, [latestDetails]);

  const warnings = useMemo(() => {
    const rows = latestDetails?.warnings;
    return Array.isArray(rows) ? rows : [];
  }, [latestDetails]);

  const collectorColumns = useMemo(
    () => [
      {
        header: (
          <TextWithDataTestId dataTestId="lbl_collector_service_type">
            <FormattedMessage id="serviceType" />
          </TextWithDataTestId>
        ),
        accessorKey: "service_type",
      },
      {
        header: (
          <TextWithDataTestId dataTestId="lbl_collector_records">
            <FormattedMessage id="recordCount" />
          </TextWithDataTestId>
        ),
        accessorKey: "records",
        cell: ({ cell }) => <FormattedNumber value={cell.getValue() || 0} />,
      },
      {
        header: (
          <TextWithDataTestId dataTestId="lbl_collector_credits">
            <FormattedMessage id="credits" />
          </TextWithDataTestId>
        ),
        accessorKey: "credits",
        cell: ({ cell }) => <FormattedNumber value={Math.round(Number(cell.getValue()) || 0)} maximumFractionDigits={0} />,
      },
      {
        header: (
          <TextWithDataTestId dataTestId="lbl_collector_tb">
            <FormattedMessage id="storageTb" />
          </TextWithDataTestId>
        ),
        accessorKey: "tb",
        cell: ({ cell, row: { original } }) => {
          const serviceType = original.service_type || "";
          if (serviceType !== "STORAGE" && serviceType !== "STAGE") {
            return CELL_EMPTY_VALUE;
          }
          const value = cell.getValue();
          if (value == null) {
            return CELL_EMPTY_VALUE;
          }
          return <FormattedNumber value={value} maximumFractionDigits={1} minimumFractionDigits={0} />;
        },
      },
      {
        header: (
          <TextWithDataTestId dataTestId="lbl_collector_finished_at">
            <FormattedMessage id="finishedAt" />
          </TextWithDataTestId>
        ),
        accessorKey: "finished_at",
        cell: ({ cell }) => {
          const value = cell.getValue();
          if (!value) {
            return CELL_EMPTY_VALUE;
          }
          // finished_at is unix UTC; format() renders in the browser local timezone.
          return format(new Date(Number(value) * 1000), EN_FULL_FORMAT_HH_MM_SS);
        },
      },
      {
        header: (
          <TextWithDataTestId dataTestId="lbl_collector_status">
            <FormattedMessage id="status" />
          </TextWithDataTestId>
        ),
        accessorKey: "status",
        cell: ({ cell, row: { original } }) => (
          <Box>
            <CollectorStatus status={cell.getValue()} />
            {original.message ? (
              <Typography variant="caption" color="textSecondary" display="block">
                <SlicedText limit={80} text={original.message} />
              </Typography>
            ) : null}
          </Box>
        ),
      },
    ],
    []
  );

  const reconcileColumns = useMemo(
    () => [
      {
        header: (
          <TextWithDataTestId dataTestId="lbl_reconcile_service_type">
            <FormattedMessage id="serviceType" />
          </TextWithDataTestId>
        ),
        accessorKey: "service_type",
        cell: ({ cell }) => cell.getValue() || CELL_EMPTY_VALUE,
      },
      {
        header: (
          <TextWithDataTestId dataTestId="lbl_reconcile_detail">
            <FormattedMessage id="detailCredits" />
          </TextWithDataTestId>
        ),
        accessorKey: "detail",
        cell: ({ cell }) => <FormattedNumber value={cell.getValue() || 0} maximumFractionDigits={4} />,
      },
      {
        header: (
          <TextWithDataTestId dataTestId="lbl_reconcile_daily">
            <FormattedMessage id="dailyCredits" />
          </TextWithDataTestId>
        ),
        accessorKey: "daily",
        cell: ({ cell }) => <FormattedNumber value={cell.getValue() || 0} maximumFractionDigits={4} />,
      },
      {
        header: (
          <TextWithDataTestId dataTestId="lbl_reconcile_delta">
            <FormattedMessage id="deltaPercent" />
          </TextWithDataTestId>
        ),
        accessorKey: "delta_pct",
        cell: ({ cell }) => {
          const value = cell.getValue();
          if (value == null) {
            return CELL_EMPTY_VALUE;
          }
          return `${Number(value).toFixed(2)}%`;
        },
      },
      {
        header: (
          <TextWithDataTestId dataTestId="lbl_reconcile_status">
            <FormattedMessage id="status" />
          </TextWithDataTestId>
        ),
        accessorKey: "status",
        cell: ({ cell, row: { original } }) => (
          <Box>
            <ReconcileStatus status={cell.getValue()} />
            {original.message ? (
              <Typography variant="caption" color="textSecondary" display="block">
                <SlicedText limit={100} text={original.message} />
              </Typography>
            ) : null}
          </Box>
        ),
      },
    ],
    []
  );

  const importPeriodStart = (latestDetails as { period_start?: number } | null)?.period_start;
  const storedPeriodEnd = (latestDetails as { period_end?: number } | null)?.period_end;
  // GCP reconcile stores exclusive calendar month-end (Sep 1). Show the
  // last successful import day instead — the real BQ data through-date.
  const importPeriodEnd =
    variant === "gcp" && latestImportAt
      ? Math.min(storedPeriodEnd || latestImportAt, latestImportAt)
      : storedPeriodEnd;
  const gcpRows = useMemo(() => {
    if (variant !== "gcp") {
      return [];
    }
    const rows = latestDetails?.reconciliation;
    if (!Array.isArray(rows)) {
      return [];
    }
    return rows.filter((row) => row.resource_type);
  }, [variant, latestDetails]);
  const gcpTotals = useMemo(() => {
    const totals = {
      source_sum: 0,
      local_sum: 0,
      target_sum: 0,
      source_count: 0,
      local_count: 0,
      target_count: 0,
    };
    let allOk = gcpRows.length > 0;
    gcpRows.forEach((row) => {
      totals.source_sum += Number(row.source_sum) || 0;
      totals.local_sum += Number(row.local_sum) || 0;
      totals.target_sum += Number(row.target_sum) || 0;
      totals.source_count += Number(row.source_count) || 0;
      totals.local_count += Number(row.local_count) || 0;
      totals.target_count += Number(row.target_count) || 0;
      if (row.status !== "ok") {
        allOk = false;
      }
    });
    return {
      ...totals,
      status: allOk ? "ok" : "mismatch",
      stageTargetMismatch: hasStageTargetCostMismatch(totals.local_sum, totals.target_sum),
    };
  }, [gcpRows]);
  const showCollectors = variant !== "gcp";
  const showSnowflakeReconcile = variant !== "gcp";
  const showGcpReconcile = variant === "gcp" && gcpRows.length > 0;

  const gcpReconcileColumns = useMemo(
    () => [
      {
        header: (
          <TextWithDataTestId dataTestId="lbl_gcp_resource_type">
            <FormattedMessage id="resourceType" />
          </TextWithDataTestId>
        ),
        accessorKey: "resource_type",
        footer: () => <FormattedMessage id="total" />,
      },
      {
        header: (
          <TextWithDataTestId dataTestId="lbl_gcp_source_sum">
            <FormattedMessage id="billingSourceSum" />
          </TextWithDataTestId>
        ),
        accessorKey: "source_sum",
        cell: ({ cell }) => (
          <FormattedNumber value={cell.getValue() || 0} maximumFractionDigits={2} />
        ),
        footer: () => (
          <FormattedNumber value={gcpTotals.source_sum} maximumFractionDigits={2} />
        ),
      },
      {
        header: (
          <TextWithDataTestId dataTestId="lbl_gcp_stage_sum">
            <FormattedMessage id="billingStageSum" />
          </TextWithDataTestId>
        ),
        accessorKey: "local_sum",
        cell: ({ cell, row }) => (
          <GcpCostValue
            value={cell.getValue()}
            highlight={hasStageTargetCostMismatch(row.original.local_sum, row.original.target_sum)}
          />
        ),
        footer: () => (
          <GcpCostValue value={gcpTotals.local_sum} highlight={gcpTotals.stageTargetMismatch} />
        ),
      },
      {
        header: (
          <TextWithDataTestId dataTestId="lbl_gcp_target_sum">
            <FormattedMessage id="billingTargetSum" />
          </TextWithDataTestId>
        ),
        accessorKey: "target_sum",
        cell: ({ cell, row }) => (
          <GcpCostValue
            value={cell.getValue()}
            highlight={hasStageTargetCostMismatch(row.original.local_sum, row.original.target_sum)}
          />
        ),
        footer: () => (
          <GcpCostValue value={gcpTotals.target_sum} highlight={gcpTotals.stageTargetMismatch} />
        ),
      },
      {
        header: (
          <TextWithDataTestId dataTestId="lbl_gcp_source_count">
            <FormattedMessage id="billingSourceCount" />
          </TextWithDataTestId>
        ),
        accessorKey: "source_count",
        cell: ({ cell }) => <FormattedNumber value={cell.getValue() || 0} />,
        footer: () => <FormattedNumber value={gcpTotals.source_count} />,
      },
      {
        header: (
          <TextWithDataTestId dataTestId="lbl_gcp_stage_count">
            <FormattedMessage id="billingStageCount" />
          </TextWithDataTestId>
        ),
        accessorKey: "local_count",
        cell: ({ cell }) => <FormattedNumber value={cell.getValue() || 0} />,
        footer: () => <FormattedNumber value={gcpTotals.local_count} />,
      },
      {
        header: (
          <TextWithDataTestId dataTestId="lbl_gcp_target_count">
            <FormattedMessage id="billingTargetCount" />
          </TextWithDataTestId>
        ),
        accessorKey: "target_count",
        cell: ({ cell }) => <FormattedNumber value={cell.getValue() || 0} />,
        footer: () => <FormattedNumber value={gcpTotals.target_count} />,
      },
      {
        header: (
          <TextWithDataTestId dataTestId="lbl_gcp_status">
            <FormattedMessage id="status" />
          </TextWithDataTestId>
        ),
        accessorKey: "status",
        cell: ({ cell }) => <ReconcileStatus status={cell.getValue()} />,
        footer: () => <ReconcileStatus status={gcpTotals.status} />,
      },
    ],
    [gcpTotals]
  );

  // Always render the collectors table (may be empty) — same as pre-tenant Snowflake.
  // Hide only while the first fetch is in flight and we have nothing to show yet.
  if (loading && !latestDetails && !isImportInProgress) {
    return null;
  }

  return (
    <Box mt={2} width="100%">
      <SubTitle>
        <FormattedMessage
          id={variant === "gcp" ? "reconciliation" : "billingImportDetails"}
        />
      </SubTitle>
      {importPeriodStart && importPeriodEnd ? (
        <Box mt={1} mb={1}>
          <KeyValueLabel
            keyMessageId="lastSuccessImportPeriod"
            value={
              <FormattedMessage
                id="fromTo"
                values={{
                  from: formatUTC(importPeriodStart, "MMM d, yyyy"),
                  to: formatUTC(importPeriodEnd, "MMM d, yyyy"),
                }}
              />
            }
            dataTestIds={{ key: "p_last_import_period", value: "value_last_import_period" }}
          />
        </Box>
      ) : null}
      {showGcpReconcile ? (
        <Box mt={1} mb={2}>
          <Table
            data={gcpRows}
            columns={gcpReconcileColumns}
            withFooter
            getRowStyle={(row) =>
              hasStageTargetCostMismatch(row.local_sum, row.target_sum)
                ? { backgroundColor: alpha(theme.palette.warning.main, 0.12) }
                : {}
            }
            counters={{
              show: false,
            }}
          />
        </Box>
      ) : null}
      {showCollectors ? (
      <Box mt={1} mb={2}>
        <Typography variant="subtitle2" gutterBottom>
          <FormattedMessage id="collectors" />
        </Typography>
        <Table
          data={collectors}
          columns={collectorColumns}
          counters={{
            show: false,
          }}
        />
      </Box>
      ) : null}
      {showSnowflakeReconcile && !isEmptyArray(reconciliation) && (
        <Box mt={1} mb={2}>
          <Typography variant="subtitle2" gutterBottom>
            <FormattedMessage id="reconciliation" />
          </Typography>
          <Table
            data={reconciliation}
            columns={reconcileColumns}
            counters={{
              show: false,
            }}
          />
        </Box>
      )}
      {!isEmptyArray(warnings) && (
        <Box mt={1}>
          <Typography variant="subtitle2" gutterBottom>
            <FormattedMessage id="importWarnings" />
          </Typography>
          {warnings.map((warning) => (
            <Typography key={warning} variant="body2" color="warning.main" sx={{ mb: 0.5 }}>
              <SlicedText limit={160} text={warning} />
            </Typography>
          ))}
        </Box>
      )}
    </Box>
  );
};

export default BillingImportDetails;
