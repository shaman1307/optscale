import { useEffect, useMemo } from "react";
import CancelIcon from "@mui/icons-material/Cancel";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import SyncIcon from "@mui/icons-material/Sync";
import WarningAmberIcon from "@mui/icons-material/WarningAmber";
import { Box, Typography } from "@mui/material";
import { FormattedMessage, FormattedNumber } from "react-intl";
import IconStatus from "components/IconStatus";
import SlicedText from "components/SlicedText";
import SubTitle from "components/SubTitle";
import Table from "components/Table";
import TextWithDataTestId from "components/TextWithDataTestId";
import { useReportImportsQuery } from "graphql/__generated__/hooks/restapi";
import { isEmptyArray } from "utils/arrays";
import { EN_FULL_FORMAT_HH_MM_SS, format } from "utils/datetime";
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

const BillingImportDetails = ({ dataSourceId }) => {
  const { data, loading, startPolling, stopPolling } = useReportImportsQuery({
    variables: {
      cloudAccountId: dataSourceId,
      showCompleted: true,
    },
  });

  const { latestDetails, isImportInProgress, overlayInProgress } = useMemo(() => {
    const imports = data?.reportImports ?? [];
    const isImportInProgress = imports.some((item) => ACTIVE_IMPORT_STATES.has(item.state));
    const activeImport = [...imports]
      .filter((item) => ACTIVE_IMPORT_STATES.has(item.state))
      .sort((a, b) => (b.created_at || 0) - (a.created_at || 0))[0];
    const activeDetails = activeImport?.details as
      | {
          collectors?: unknown[];
          reconciliation?: unknown[];
          warnings?: unknown[];
        }
      | null
      | undefined;
    const hasActiveCollectors =
      Array.isArray(activeDetails?.collectors) && activeDetails.collectors.length > 0;

    const withDetails = imports
      .filter((item) => {
        const details = item.details as
          | {
              collectors?: unknown[];
              reconciliation?: unknown[];
              warnings?: unknown[];
            }
          | null
          | undefined;
        if (item.state !== "completed" || !details) {
          return false;
        }
        return (
          (Array.isArray(details.collectors) && details.collectors.length > 0) ||
          (Array.isArray(details.reconciliation) && details.reconciliation.length > 0) ||
          (Array.isArray(details.warnings) && details.warnings.length > 0)
        );
      })
      .sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
    const completedDetails = withDetails[0]?.details ?? null;

    if (hasActiveCollectors) {
      return {
        latestDetails: activeDetails,
        isImportInProgress: true,
        overlayInProgress: false,
      };
    }
    return {
      latestDetails: completedDetails,
      isImportInProgress,
      // Active import without progressive details yet — mark previous rows live.
      overlayInProgress: Boolean(isImportInProgress && completedDetails),
    };
  }, [data]);

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
        cell: ({ cell }) => (
          <FormattedNumber value={Math.round(Number(cell.getValue()) || 0)} maximumFractionDigits={0} />
        ),
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

  if (loading && !latestDetails) {
    return null;
  }

  if (!latestDetails || (isEmptyArray(collectors) && isEmptyArray(reconciliation) && isEmptyArray(warnings))) {
    return null;
  }

  return (
    <Box mt={2} width="100%">
      <SubTitle>
        <FormattedMessage id="billingImportDetails" />
      </SubTitle>
      {!isEmptyArray(collectors) && (
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
      )}
      {!isEmptyArray(reconciliation) && (
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
