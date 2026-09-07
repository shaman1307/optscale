import { useEffect, useMemo } from "react";
import { useDispatch } from "react-redux";
import { getRegionExpenses } from "api";
import { GET_REGION_EXPENSES } from "api/restapi/actionTypes";
import RegionExpenses from "components/RegionExpenses";
import { useApiData } from "hooks/useApiData";
import { useApiState } from "hooks/useApiState";
import { useOrganizationInfo } from "hooks/useOrganizationInfo";
import { useReactiveDefaultDateRange } from "hooks/useReactiveDefaultDateRange";
import { DATE_RANGE_TYPE, INVOICE_MONTHS_FILTER } from "utils/constants";
import { normalizeInvoiceMonths } from "utils/costPeriod";
import { getSearchParams } from "utils/network";

const RegionExpensesContainer = () => {
  const dispatch = useDispatch();
  const { organizationId } = useOrganizationInfo();

  const [startDateTimestamp, endDateTimestamp] = useReactiveDefaultDateRange(DATE_RANGE_TYPE.EXPENSES);
  const invoiceMonths = normalizeInvoiceMonths(getSearchParams()[INVOICE_MONTHS_FILTER]);
  const periodParams = useMemo(
    () =>
      invoiceMonths.length
        ? { invoiceMonths }
        : { startDate: startDateTimestamp, endDate: endDateTimestamp },
    [invoiceMonths, startDateTimestamp, endDateTimestamp]
  );

  const { isLoading, shouldInvoke } = useApiState(GET_REGION_EXPENSES, {
    ...periodParams,
    organizationId,
  });

  useEffect(() => {
    if (shouldInvoke) {
      dispatch(getRegionExpenses(organizationId, periodParams));
    }
  }, [dispatch, organizationId, shouldInvoke, periodParams]);

  const {
    apiData: { expenses = {} },
  } = useApiData(GET_REGION_EXPENSES);

  return <RegionExpenses isLoading={isLoading} expenses={expenses} />;
};

export default RegionExpensesContainer;
