import { useEffect } from "react";
import { useDispatch } from "react-redux";
import { getRawExpenses } from "api";
import { GET_RAW_EXPENSES } from "api/restapi/actionTypes";
import ResourceRawExpenses from "components/ResourceRawExpenses";
import { useApiData } from "hooks/useApiData";
import { useApiState } from "hooks/useApiState";
import { isIncompleteBillingPeriod } from "utils/costPeriod";

const ResourceRawExpensesContainer = ({
  resourceId,
  startDate,
  endDate,
  invoiceMonths = [],
  periodType,
  expensesMode,
}) => {
  const dispatch = useDispatch();
  const requestParams = { startDate, endDate, invoiceMonths, periodType };

  const { isLoading, shouldInvoke } = useApiState(GET_RAW_EXPENSES, { ...requestParams, resourceId });

  useEffect(() => {
    if (shouldInvoke && !isIncompleteBillingPeriod(requestParams)) {
      dispatch(getRawExpenses(resourceId, requestParams));
    }
  }, [dispatch, shouldInvoke, resourceId, startDate, endDate, invoiceMonths, periodType]);

  const {
    apiData: { raw_expenses: expenses = [], total_cost: totalCost = 0 },
  } = useApiData(GET_RAW_EXPENSES);

  return (
    <ResourceRawExpenses
      startDate={startDate}
      endDate={endDate}
      shownExpenses={totalCost}
      expenses={expenses}
      isLoading={isLoading}
      expensesMode={expensesMode}
    />
  );
};

export default ResourceRawExpensesContainer;
