import { useEffect } from "react";
import { Typography } from "@mui/material";
import { FormattedMessage } from "react-intl";
import { useDispatch } from "react-redux";
import { getCloudsExpenses } from "api";
import { GET_CLOUDS_EXPENSES } from "api/restapi/actionTypes";
import ExpensesBreakdown from "components/ExpensesBreakdown";
import { useApiState } from "hooks/useApiState";
import { useAsymptoticLoadPercent } from "hooks/useAsymptoticLoadPercent";
import { useExpensesBreakdownRequestParams } from "hooks/useExpensesBreakdownRequestParams";
import { useExpensesData } from "hooks/useExpensesData";
import { isIncompleteBillingPeriod } from "utils/costPeriod";

const COST_EXPLORER_EXPECTED_LOAD_SEC = 45;

const CloudExpensesBreakdownContainer = ({ type, entityId: cloudAccountId }) => {
  const dispatch = useDispatch();

  const {
    filterBy,
    startDateTimestamp,
    endDateTimestamp,
    invoiceMonths,
    breakdown,
    name,
    total,
    previousTotal,
    filteredBreakdown,
    dataSourceType,
  } = useExpensesData(GET_CLOUDS_EXPENSES);

  const [requestParams, applyFilter, updateFilter] = useExpensesBreakdownRequestParams({
    filterBy,
    startDateTimestamp,
    endDateTimestamp,
    invoiceMonths,
  });

  const { isLoading, shouldInvoke } = useApiState(GET_CLOUDS_EXPENSES, { ...requestParams, cloudAccountId });
  const loadPercent = useAsymptoticLoadPercent(isLoading, COST_EXPLORER_EXPECTED_LOAD_SEC, requestParams);

  useEffect(() => {
    if (shouldInvoke && !isIncompleteBillingPeriod(requestParams)) {
      dispatch(getCloudsExpenses(cloudAccountId, requestParams));
    }
  }, [cloudAccountId, dispatch, requestParams, shouldInvoke]);

  const loadProgress =
    isLoading && loadPercent < 100 ? (
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }} data-test-id="cost_explorer_load_progress">
        <FormattedMessage id="resourcesPageLoadingProgress" values={{ value: loadPercent }} />
      </Typography>
    ) : null;

  return (
    <ExpensesBreakdown
      entityId={cloudAccountId}
      filterBy={filterBy}
      type={type}
      breakdown={breakdown}
      total={total}
      previousTotal={previousTotal}
      filteredBreakdown={filteredBreakdown}
      startDateTimestamp={startDateTimestamp}
      endDateTimestamp={endDateTimestamp}
      invoiceMonths={requestParams.invoiceMonths || []}
      isLoading={isLoading}
      loadProgress={loadProgress}
      onApply={applyFilter}
      updateFilter={updateFilter}
      name={name}
      dataSourceType={dataSourceType}
    />
  );
};

export default CloudExpensesBreakdownContainer;
