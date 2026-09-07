import { useEffect } from "react";
import { Typography } from "@mui/material";
import { FormattedMessage } from "react-intl";
import { useDispatch } from "react-redux";
import { getPoolExpenses } from "api";
import { GET_POOLS_EXPENSES } from "api/restapi/actionTypes";
import CostExplorer from "components/CostExplorer";
import ExpensesBreakdown from "components/ExpensesBreakdown";
import VirtualTagExpensesBreakdownContainer from "containers/VirtualTagExpensesBreakdownContainer";
import { useApiState } from "hooks/useApiState";
import { useAsymptoticLoadPercent } from "hooks/useAsymptoticLoadPercent";
import { useExpensesBreakdownRequestParams } from "hooks/useExpensesBreakdownRequestParams";
import { useExpensesData } from "hooks/useExpensesData";
import { useOrganizationInfo } from "hooks/useOrganizationInfo";
import { COST_EXPLORER } from "utils/constants";
import { isIncompleteBillingPeriod } from "utils/costPeriod";
import { isVirtualTagBreakdown } from "utils/virtualTagBreakdown";

// Expected wall time for pools_expenses on large orgs (Profitero-scale ~20–30s).
const COST_EXPLORER_EXPECTED_LOAD_SEC = 45;

const PoolExpensesBreakdownContainer = ({ type, entityId: poolId }) => {
  const dispatch = useDispatch();

  const { organizationPoolId } = useOrganizationInfo();

  const { filterBy, startDateTimestamp, endDateTimestamp, invoiceMonths, breakdown, name, total, previousTotal, filteredBreakdown } =
    useExpensesData(GET_POOLS_EXPENSES);

  const [requestParams, applyFilter, updateFilter] = useExpensesBreakdownRequestParams({
    filterBy,
    startDateTimestamp,
    endDateTimestamp,
    invoiceMonths,
  });

  const getIdValue = () => poolId || organizationPoolId;

  const idValue = getIdValue();

  const { isLoading, shouldInvoke } = useApiState(GET_POOLS_EXPENSES, { ...requestParams, poolId: idValue });
  const loadPercent = useAsymptoticLoadPercent(isLoading, COST_EXPLORER_EXPECTED_LOAD_SEC, requestParams);
  const isVirtualTag = isVirtualTagBreakdown(filterBy);

  useEffect(() => {
    if (shouldInvoke && !isIncompleteBillingPeriod(requestParams) && !isVirtualTag) {
      dispatch(getPoolExpenses(idValue, requestParams));
    }
  }, [dispatch, idValue, requestParams, shouldInvoke, isVirtualTag]);

  const loadProgress =
    isLoading && loadPercent < 100 ? (
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }} data-test-id="cost_explorer_load_progress">
        <FormattedMessage id="resourcesPageLoadingProgress" values={{ value: loadPercent }} />
      </Typography>
    ) : null;

  const render = () => {
    if (type === COST_EXPLORER && isVirtualTag) {
      return (
        <VirtualTagExpensesBreakdownContainer
          filterBy={filterBy}
          startDateTimestamp={startDateTimestamp}
          endDateTimestamp={endDateTimestamp}
          invoiceMonths={requestParams.invoiceMonths || []}
          onApply={applyFilter}
          skipRequest={isIncompleteBillingPeriod(requestParams)}
        />
      );
    }
    if (type === COST_EXPLORER) {
      return filterBy ? (
        <ExpensesBreakdown
          entityId={poolId}
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
        />
      ) : (
        <CostExplorer
          total={total}
          previousTotal={previousTotal}
          breakdown={breakdown}
          organizationName={name}
          isLoading={isLoading}
          loadProgress={loadProgress}
          onApply={applyFilter}
          startDateTimestamp={startDateTimestamp}
          endDateTimestamp={endDateTimestamp}
          invoiceMonths={requestParams.invoiceMonths || []}
        />
      );
    }
    return (
      <ExpensesBreakdown
        entityId={poolId}
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
      />
    );
  };

  return render();
};

export default PoolExpensesBreakdownContainer;
