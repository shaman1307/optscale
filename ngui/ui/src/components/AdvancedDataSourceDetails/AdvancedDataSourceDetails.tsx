import { useMemo } from "react";
import CancelIcon from "@mui/icons-material/Cancel";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import SyncIcon from "@mui/icons-material/Sync";
import { Box, Grid, Link, Typography } from "@mui/material";
import { FormattedMessage } from "react-intl";
import BillingImportDetails from "components/AdvancedDataSourceDetails/BillingImportDetails";
import IconStatus from "components/IconStatus";
import KeyValueLabel from "components/KeyValueLabel/KeyValueLabel";
import SlicedText from "components/SlicedText";
import { ResourceDuplicatesModal } from "components/SideModalManager/SideModals";
import SubTitle from "components/SubTitle";
import SummaryList from "components/SummaryList";
import Table from "components/Table";
import { useReportImportsQuery, useResourceDuplicatesQuery } from "graphql/__generated__/hooks/restapi";
import { useOpenSideModal } from "hooks/useOpenSideModal";
import { isEmptyArray } from "utils/arrays";
import { discoveryStatus, lastTimeLocal, resourceType } from "utils/columns";
import { DISCOVERY_STATUS } from "utils/columns/discoveryStatus";
import { GCP_CNR, GCP_TENANT, SNOWFLAKE, SNOWFLAKE_TENANT } from "utils/constants";
import { BILLING_IMPORT_STATUS, getBillingImportStatus } from "utils/dataSources";
import { getTimeDistance, formatUTC } from "utils/datetime";

const ResourceTypeStatusTable = ({
  titleMessageId,
  infos,
  timeMessageId = "lastDiscoveryAt",
  timeHeaderDataTestId = "lbl_last_discovery_at",
}) => {
  const columns = useMemo(
    () => [
      resourceType({
        style: {
          minWidth: "150px",
        },
      }),
      lastTimeLocal({
        headerDataTestId: timeHeaderDataTestId,
        messageId: timeMessageId,
        accessorKey: "last_discovery_at",
        style: {
          minWidth: "170px",
        },
      }),
      discoveryStatus(),
    ],
    [timeHeaderDataTestId, timeMessageId]
  );

  const tableData = useMemo(() => {
    const getStatus = (lastDiscoveryAt, lastErrorAt) => {
      if (lastDiscoveryAt === 0 && lastErrorAt === 0) {
        return DISCOVERY_STATUS.UNKNOWN;
      }

      return lastDiscoveryAt > lastErrorAt ? DISCOVERY_STATUS.SUCCESS : DISCOVERY_STATUS.ERROR;
    };

    return infos.map((info) => ({
      ...info,
      status: getStatus(info.last_discovery_at, info.last_error_at),
    }));
  }, [infos]);

  return (
    <>
      <SubTitle>
        <FormattedMessage id={titleMessageId} />
      </SubTitle>
      <Table
        data={tableData}
        columns={columns}
        counters={{
          show: false,
        }}
      />
    </>
  );
};

type ReconciliationRow = {
  resource_type?: string;
};

const getReconciliationRows = (details?: Record<string, unknown> | null): ReconciliationRow[] => {
  const rows = details?.reconciliation;
  if (!Array.isArray(rows)) {
    return [];
  }
  return rows.filter((row): row is ReconciliationRow => Boolean(row) && typeof row === "object");
};

const BillingResources = ({ dataSourceId, lastImportAt, lastImportAttemptAt, lastImportAttemptError }) => {
  const { data } = useReportImportsQuery({
    variables: {
      cloudAccountId: dataSourceId,
      showCompleted: true,
    },
    skip: !dataSourceId,
  });

  const infos = useMemo(() => {
    const imports = data?.reportImports ?? [];
    const completed = [...imports]
      .filter((item) => {
        if (item.state !== "completed") {
          return false;
        }
        return getReconciliationRows(item.details).some((row) => row?.resource_type);
      })
      .sort((a, b) => (b.created_at || 0) - (a.created_at || 0))[0];
    const rows = getReconciliationRows(completed?.details);
    const lastErrorAt = lastImportAttemptError && lastImportAt < lastImportAttemptAt ? lastImportAttemptAt : 0;

    return rows
      .filter((row) => row?.resource_type)
      .map((row) => ({
        resource_type: row.resource_type,
        last_discovery_at: lastImportAt || 0,
        last_error_at: lastErrorAt || 0,
        last_error: lastImportAttemptError,
      }));
  }, [data, lastImportAt, lastImportAttemptAt, lastImportAttemptError]);

  if (isEmptyArray(infos)) {
    return null;
  }

  return (
    <ResourceTypeStatusTable
      titleMessageId="billingResources"
      infos={infos}
      timeMessageId="lastImportAt"
      timeHeaderDataTestId="lbl_last_billing_import_at"
    />
  );
};

const ResourceDuplicates = ({ dataSourceId }) => {
  const openSideModal = useOpenSideModal();
  const { data, loading, error } = useResourceDuplicatesQuery({
    variables: { cloudAccountId: dataSourceId },
    skip: !dataSourceId,
  });

  const count = data?.resourceDuplicates?.count;
  const renderValue = () => {
    if (loading) {
      return "...";
    }
    if (error || count === undefined) {
      return (
        <span data-test-id="value_resource_duplicates_error">
          <FormattedMessage id="failedToLoadResourceDuplicates" />
        </span>
      );
    }
    return (
      <Box component="span" display="inline-flex" alignItems="center" gap={1}>
        <Typography
          component="span"
          variant="body2"
          color={count ? "error" : "inherit"}
          data-test-id="value_resource_duplicates_count"
        >
          {count}
        </Typography>
        <Link
          component="button"
          type="button"
          variant="body2"
          data-test-id="btn_view_resource_duplicates"
          onClick={() => openSideModal(ResourceDuplicatesModal, { dataSourceId })}
        >
          <FormattedMessage id="view" />
        </Link>
      </Box>
    );
  };

  return (
    <SummaryList
      titleMessage={<FormattedMessage id="resourceDuplicates" />}
      items={
        <KeyValueLabel
          key="resourceDuplicatesCount"
          keyMessageId="duplicateGroups"
          value={renderValue()}
          dataTestIds={{ key: "p_resource_duplicates_count", value: "value_resource_duplicates" }}
        />
      }
    />
  );
};

const Status = ({ timestamp, attemptTimestamp, error }) => {
  const status = getBillingImportStatus({
    timestamp,
    attemptTimestamp,
    error,
  });

  if (status === BILLING_IMPORT_STATUS.SUCCESS) {
    return <IconStatus icon={CheckCircleIcon} color="success" labelMessageId="completed" />;
  }
  if (status === BILLING_IMPORT_STATUS.ERROR) {
    return <IconStatus icon={CancelIcon} color="error" labelMessageId="failed" />;
  }

  return "-";
};

const BillingImportStatus = ({ dataSourceId, timestamp, attemptTimestamp, error }) => {
  const { data } = useReportImportsQuery({
    variables: {
      cloudAccountId: dataSourceId,
      showCompleted: true,
    },
    skip: !dataSourceId,
    pollInterval: 10000,
  });

  const importInProgress = (data?.reportImports ?? []).find((item) => item.state === "in_progress");
  if (importInProgress) {
    return <IconStatus icon={SyncIcon} color="primary" labelMessageId="inProgress" />;
  }

  const queuedImport = (data?.reportImports ?? []).find((item) => item.state === "scheduled");
  if (queuedImport) {
    return <IconStatus icon={SyncIcon} color="primary" labelMessageId="queued" />;
  }

  return <Status timestamp={timestamp} attemptTimestamp={attemptTimestamp} error={error} />;
};

const AdvancedDataSourceDetails = ({
  dataSourceId,
  dataSourceType,
  parentId,
  accountId,
  lastImportAttemptAt,
  lastImportAt,
  lastImportAttemptError,
  lastMetricsRetrieval,
  lastMetricsRetrievalAttempt,
  lastGettingMetricAttemptError,
  discoveryInfos = [],
  billingPeriodStart,
  billingPeriodEnd,
}) => {
  const isSnowflakeTenant = dataSourceType === SNOWFLAKE_TENANT;
  const isSnowflakeChild = dataSourceType === SNOWFLAKE && Boolean(parentId);
  const isGcp = dataSourceType === GCP_CNR || dataSourceType === GCP_TENANT;
  const showSnowflakeBillingDetails = dataSourceType === SNOWFLAKE || isSnowflakeTenant;
  const showGcpBillingDetails = isGcp;
  const showResourceDuplicates = isGcp;
  // Tenant import owns report_import.details; children read the parent import and
  // filter collectors by account_locator (accountId).
  const billingImportCloudAccountId = isSnowflakeChild ? parentId : dataSourceId;
  const billingAccountLocator = isSnowflakeChild ? accountId : null;

  return (
    <>
      <Box display="flex" flexWrap="wrap" rowGap={1} columnGap={16}>
        <Box>
          <SummaryList
            titleMessage={<FormattedMessage id="billingImport" />}
            items={
              <>
                <KeyValueLabel
                  key="lastImportAt"
                  keyMessageId="lastBillingReportProcessed"
                  value={
                    <FormattedMessage
                      id={!lastImportAt ? "never" : "valueAgo"}
                      values={{
                        value: lastImportAt ? getTimeDistance(lastImportAt) : null,
                      }}
                    />
                  }
                  dataTestIds={{ key: "p_last_billing_report_processed", value: "value_last_billing_report_processed" }}
                />
                <KeyValueLabel
                  key="lastImportAttemptAt"
                  keyMessageId="lastBillingReportAttempt"
                  value={
                    <FormattedMessage
                      id={!lastImportAttemptAt ? "never" : "valueAgo"}
                      values={{
                        value: lastImportAttemptAt ? getTimeDistance(lastImportAttemptAt) : null,
                      }}
                    />
                  }
                  dataTestIds={{ key: "p_last_billing_report_attempt", value: "value_last_billing_report_attempt" }}
                />
                <KeyValueLabel
                  key="status"
                  keyMessageId="status"
                  value={
                    <BillingImportStatus
                      dataSourceId={billingImportCloudAccountId || dataSourceId}
                      timestamp={lastImportAt}
                      attemptTimestamp={lastImportAttemptAt}
                      error={lastImportAttemptError}
                    />
                  }
                  dataTestIds={{ key: "p_last_billing_report_status", value: "value_last_billing_report_status" }}
                />
                {billingPeriodStart && billingPeriodEnd ? (
                  <KeyValueLabel
                    key="billingDataPeriod"
                    keyMessageId="billingDataPeriod"
                    value={
                      <FormattedMessage
                        id="fromTo"
                        values={{
                          // Include month name + year so multi-year ranges are unambiguous.
                          from: formatUTC(billingPeriodStart, "MMM d, yyyy"),
                          to: formatUTC(billingPeriodEnd, "MMM d, yyyy"),
                        }}
                      />
                    }
                    dataTestIds={{ key: "p_billing_data_period", value: "value_billing_data_period" }}
                  />
                ) : null}
                {lastImportAttemptError ? (
                  <KeyValueLabel
                    key="reason"
                    keyMessageId="reason"
                    value={<SlicedText limit={50} text={lastImportAttemptError} />}
                    dataTestIds={{ key: "p_last_billing_report_reason", value: "value_last_billing_report_reason" }}
                  />
                ) : null}
              </>
            }
          />
        </Box>
        <Box>
          <SummaryList
            titleMessage={<FormattedMessage id="monitoringMetricsImport" />}
            items={
              <>
                <KeyValueLabel
                  key="lastMetricsRetrieval"
                  keyMessageId="lastMetricsRetrieval"
                  value={
                    <FormattedMessage
                      id={!lastMetricsRetrieval ? "never" : "valueAgo"}
                      values={{
                        value: lastMetricsRetrieval ? getTimeDistance(lastMetricsRetrieval) : null,
                      }}
                    />
                  }
                  dataTestIds={{ key: "p_last_getting_metrics_at", value: "value_last_getting_metrics_at" }}
                />
                <KeyValueLabel
                  key="lastMetricsRetrievalAttempt"
                  keyMessageId="lastMetricsRetrievalAttempt"
                  value={
                    <FormattedMessage
                      id={!lastMetricsRetrievalAttempt ? "never" : "valueAgo"}
                      values={{
                        value: lastMetricsRetrievalAttempt ? getTimeDistance(lastMetricsRetrievalAttempt) : null,
                      }}
                    />
                  }
                  dataTestIds={{ key: "p_last_getting_metrics_attempt_at", value: "value_last_getting_metrics_attempt_at" }}
                />
                <KeyValueLabel
                  key="status"
                  keyMessageId="status"
                  value={
                    <Status
                      timestamp={lastMetricsRetrieval}
                      attemptTimestamp={lastMetricsRetrievalAttempt}
                      error={lastGettingMetricAttemptError}
                    />
                  }
                  dataTestIds={{ key: "p_last_metrics_report_status", value: "value_last_metrics_report_status" }}
                />
                {lastGettingMetricAttemptError && lastMetricsRetrieval < lastMetricsRetrievalAttempt ? (
                  <KeyValueLabel
                    key="reason"
                    keyMessageId="reason"
                    value={<SlicedText limit={50} text={lastGettingMetricAttemptError} />}
                    dataTestIds={{ key: "p_last_metrics_report_reason", value: "value_last_metrics_report_reason" }}
                  />
                ) : null}
              </>
            }
          />
        </Box>
        {showResourceDuplicates ? (
          <Box>
            <ResourceDuplicates dataSourceId={dataSourceId} />
          </Box>
        ) : null}
        {!isEmptyArray(discoveryInfos) && (
          <Grid item xs={12}>
            <ResourceTypeStatusTable titleMessageId="discovery" infos={discoveryInfos} />
          </Grid>
        )}
        {showGcpBillingDetails && dataSourceId ? (
          <Grid item xs={12}>
            <BillingResources
              dataSourceId={dataSourceId}
              lastImportAt={lastImportAt}
              lastImportAttemptAt={lastImportAttemptAt}
              lastImportAttemptError={lastImportAttemptError}
            />
          </Grid>
        ) : null}
      </Box>
      {showSnowflakeBillingDetails && billingImportCloudAccountId ? (
        <BillingImportDetails dataSourceId={billingImportCloudAccountId} accountLocator={billingAccountLocator} />
      ) : null}
      {showGcpBillingDetails && dataSourceId ? (
        <BillingImportDetails dataSourceId={dataSourceId} variant="gcp" />
      ) : null}
    </>
  );
};

export default AdvancedDataSourceDetails;
