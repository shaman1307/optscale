import { useApiData } from "hooks/useApiData";
import { useReactiveDefaultDateRange } from "hooks/useReactiveDefaultDateRange";
import { DATE_RANGE_TYPE, FILTER_BY, INVOICE_MONTHS_FILTER } from "utils/constants";
import { normalizeInvoiceMonths } from "utils/costPeriod";
import { getSearchParams } from "utils/network";

export const useExpensesData = (label) => {
  const {
    apiData: { expenses = {} },
  } = useApiData(label);

  const queryParams = getSearchParams();

  const { [FILTER_BY]: filterBy } = queryParams;
  const invoiceMonths = normalizeInvoiceMonths(queryParams[INVOICE_MONTHS_FILTER]);

  const [startDateTimestamp, endDateTimestamp] = useReactiveDefaultDateRange(DATE_RANGE_TYPE.EXPENSES);

  const {
    name,
    total = 0,
    previous_total: previousTotal = 0,
    breakdown = {},
    [filterBy]: filteredBreakdown = [],
    id: poolId,
    type,
  } = expenses;

  return {
    filterBy,
    startDateTimestamp,
    endDateTimestamp,
    invoiceMonths,
    name,
    total,
    previousTotal,
    breakdown,
    filteredBreakdown,
    poolId,
    dataSourceType: type,
  };
};
