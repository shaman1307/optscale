import { useMemo } from "react";
import CancelIcon from "@mui/icons-material/Cancel";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
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
import { CELL_EMPTY_VALUE } from "utils/tables";

const CollectorStatus = ({ status }) => {
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
  const { data, loading } = useReportImportsQuery({
    variables: {
      cloudAccountId: dataSourceId,
      showCompleted: true,
    },
  });

  const latestDetails = useMemo(() => {
    const imports = data?.reportImports ?? [];
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
    return withDetails[0]?.details ?? null;
  }, [data]);

  const collectors = useMemo(() => {
    const rows = latestDetails?.collectors;
    return Array.isArray(rows) ? rows : [];
  }, [latestDetails]);

  const reconciliation = useMemo(() => {
    const rows = latestDetails?.reconciliation;
    return Array.isArray(rows) ? rows.filter((row) => row?.service_type) : [];
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
        cell: ({ cell }) => <FormattedNumber value={cell.getValue() || 0} maximumFractionDigits={4} />,
      },
      {
        header: (
          <TextWithDataTestId dataTestId="lbl_collector_tb">
            <FormattedMessage id="storage" />
          </TextWithDataTestId>
        ),
        accessorKey: "tb",
        cell: ({ cell, row: { original } }) => {
          const serviceType = original.service_type || "";
          if (serviceType !== "DATABASE_STORAGE" && serviceType !== "STAGE_STORAGE") {
            return CELL_EMPTY_VALUE;
          }
          const value = cell.getValue();
          if (value == null) {
            return CELL_EMPTY_VALUE;
          }
          return <FormattedNumber value={value} maximumFractionDigits={4} />;
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
        cell: ({ cell }) => <ReconcileStatus status={cell.getValue()} />,
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
