import { useEffect, useMemo, useState } from "react";
import { Grid, Typography } from "@mui/material";
import { FormattedMessage, useIntl } from "react-intl";
import { useDispatch } from "react-redux";
import { getCleanExpenses, REST_API_URL } from "api";
import { GET_EXPENSES_DAILY_BREAKDOWN, GET_EXPENSES_SUMMARY } from "api/restapi/actionTypes";
import CleanExpensesTable from "components/CleanExpensesTable";
import CleanExpensesTableGroup from "components/CleanExpensesTableGroup";
import ExpensesDailyBreakdown from "components/ExpensesDailyBreakdown";
import { ExpensesDailyBreakdownByMockup } from "components/ExpensesDailyBreakdownBy";
import InlineSeverityAlert from "components/InlineSeverityAlert";
import TableLoader from "components/TableLoader";
import ExpensesDailyBreakdownByContainer from "containers/ExpensesDailyBreakdownByContainer";
import { useApiState } from "hooks/useApiState";
import { useFetchAndDownload } from "hooks/useFetchAndDownload";
import { useOrganizationInfo } from "hooks/useOrganizationInfo";
import CleanExpensesService, { mapCleanExpensesRequestParamsToApiParams } from "services/CleanExpensesService";
import { getLength, isEmptyArray } from "utils/arrays";
import { stringifySearchParams } from "utils/network";

const shouldRenderLimitWarning = (limit, expenses) => getLength(expenses) === limit;

// Expected wall times (seconds) from Profitero Resources page profiling.
// Progress is weighted by these, not split evenly across steps.
const LOAD_STEP_WEIGHTS_SEC = {
  filters: 44,
  summary: 28,
  breakdown: 57,
  cleanExpenses: 36,
} as const;

const LOAD_STEPS_TOTAL_WEIGHT = Object.values(LOAD_STEP_WEIGHTS_SEC).reduce((sum, weight) => sum + weight, 0);

const LimitWarning = ({ limit }) => {
  const intl = useIntl();

  return (
    <InlineSeverityAlert
      messageId="rowsLimitWarning"
      messageValues={{
        entities: intl.formatMessage({ id: "resources" }).toLocaleLowerCase(),
        count: limit,
      }}
      sx={{
        mb: 1,
      }}
    />
  );
};

const ResourcesPageLoadProgress = ({ percent }: { percent: number }) => (
  <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }} data-test-id="resources_page_load_progress">
    <FormattedMessage id="resourcesPageLoadingProgress" values={{ value: percent }} />
  </Typography>
);

// In-flight steps must not reach their full weight from wall-clock alone:
// linear elapsed/weight hit 100% after ~max(weight) and then stuck at 99%
// while the slowest request still ran 30–40s. Asymptote approaches the weight
// but only completed steps grant the full share.
const inFlightFraction = (elapsedMs: number, weightSec: number) => 1 - Math.exp(-elapsedMs / (weightSec * 1000));

const getWeightedLoadPercent = (steps: { weightSec: number; isLoading: boolean }[], elapsedMs: number) => {
  const contributed = steps.reduce((sum, { weightSec, isLoading }) => {
    if (!isLoading) {
      return sum + weightSec;
    }
    return sum + weightSec * inFlightFraction(elapsedMs, weightSec);
  }, 0);

  return Math.min(99, Math.round((contributed / LOAD_STEPS_TOTAL_WEIGHT) * 100));
};

const CleanExpensesBreakdownContainer = ({ requestParams, isFilterValuesLoading = false }) => {
  const dispatch = useDispatch();

  const { organizationId } = useOrganizationInfo();
  const { isFileDownloading, fetchAndDownload } = useFetchAndDownload();

  const downloadResources = (format) => {
    // Intentionally keep limit in requestParams
    const params = { ...requestParams, format };

    fetchAndDownload({
      url: `${REST_API_URL}/organizations/${organizationId}/clean_expenses?${stringifySearchParams(
        mapCleanExpensesRequestParamsToApiParams(params)
      )}`,
      fallbackFilename: `resources_list.${format}`,
    });
  };

  const { useGet } = CleanExpensesService();
  const { isLoading: isCleanExpensesLoading, data: apiData } = useGet({ params: requestParams });
  const { clean_expenses: expenses = [], total_count: totalResourcesCount } = apiData;

  const { isLoading: isSummaryLoading } = useApiState(GET_EXPENSES_SUMMARY);
  const { isLoading: isBreakdownLoading } = useApiState(GET_EXPENSES_DAILY_BREAKDOWN);

  const loadSteps = useMemo(
    () => [
      { weightSec: LOAD_STEP_WEIGHTS_SEC.filters, isLoading: isFilterValuesLoading },
      { weightSec: LOAD_STEP_WEIGHTS_SEC.summary, isLoading: isSummaryLoading },
      { weightSec: LOAD_STEP_WEIGHTS_SEC.breakdown, isLoading: isBreakdownLoading },
      { weightSec: LOAD_STEP_WEIGHTS_SEC.cleanExpenses, isLoading: isCleanExpensesLoading },
    ],
    [isFilterValuesLoading, isSummaryLoading, isBreakdownLoading, isCleanExpensesLoading]
  );

  const isPageLoading = loadSteps.some(({ isLoading }) => isLoading);

  const [elapsedMs, setElapsedMs] = useState(0);

  useEffect(() => {
    if (!isPageLoading) {
      setElapsedMs(0);
      return undefined;
    }

    const startedAt = Date.now();
    setElapsedMs(0);
    const intervalId = window.setInterval(() => {
      setElapsedMs(Date.now() - startedAt);
    }, 250);

    return () => window.clearInterval(intervalId);
  }, [isPageLoading, requestParams]);

  const loadPercent = isPageLoading ? getWeightedLoadPercent(loadSteps, elapsedMs) : 100;

  const startDateTimestamp = Number(requestParams.startDate);
  const endDateTimestamp = Number(requestParams.endDate);

  const renderExpensesBreakdownTable = () => {
    if (isCleanExpensesLoading) {
      return <TableLoader columnsCounter={1} showHeader />;
    }
    if (isEmptyArray(expenses)) {
      return <CleanExpensesTable expenses={expenses} />;
    }

    return (
      <>
        {shouldRenderLimitWarning(requestParams.limit, expenses) && <LimitWarning limit={requestParams.limit} />}
        <CleanExpensesTableGroup
          startDateTimestamp={startDateTimestamp}
          endDateTimestamp={endDateTimestamp}
          expenses={expenses}
          onSideModalClose={() =>
            dispatch(getCleanExpenses(organizationId, mapCleanExpensesRequestParamsToApiParams(requestParams)))
          }
          downloadResources={downloadResources}
          isDownloadingResources={isFileDownloading}
          totalResourcesCount={totalResourcesCount}
        />
      </>
    );
  };

  return (
    <Grid container spacing={2}>
      <Grid item xs={12}>
        <ExpensesDailyBreakdown
          container={<ExpensesDailyBreakdownByContainer cleanExpensesRequestParams={requestParams} />}
          mockup={<ExpensesDailyBreakdownByMockup startDateTimestamp={startDateTimestamp} />}
        />
      </Grid>
      <Grid item xs={12}>
        {isPageLoading && <ResourcesPageLoadProgress percent={loadPercent} />}
        {renderExpensesBreakdownTable()}
      </Grid>
    </Grid>
  );
};

export default CleanExpensesBreakdownContainer;
