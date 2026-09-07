import { useEffect, useState } from "react";
import { INVOICE_MONTHS_FILTER } from "utils/constants";
import { COST_PERIOD_BILLING, normalizeInvoiceMonths } from "utils/costPeriod";
import { getSearchParams, updateSearchParams } from "utils/network";

export const useExpensesBreakdownRequestParams = ({
  filterBy,
  startDateTimestamp,
  endDateTimestamp,
  invoiceMonths: invoiceMonthsProp,
}) => {
  const [requestParams, setRequestParams] = useState(() => {
    const fromUrl = normalizeInvoiceMonths(getSearchParams()[INVOICE_MONTHS_FILTER]);
    const invoiceMonths = invoiceMonthsProp?.length ? invoiceMonthsProp : fromUrl;
    if (invoiceMonths.length) {
      return {
        filterBy,
        invoiceMonths,
      };
    }
    return {
      filterBy,
      startDate: startDateTimestamp,
      endDate: endDateTimestamp,
    };
  });

  useEffect(() => {
    setRequestParams((curr) => ({ ...curr, filterBy }));
  }, [filterBy]);

  useEffect(() => {
    if (requestParams.invoiceMonths?.length) {
      updateSearchParams({
        filterBy: requestParams.filterBy,
        [INVOICE_MONTHS_FILTER]: requestParams.invoiceMonths,
        startDate: null,
        endDate: null,
      });
      return;
    }
    if (Array.isArray(requestParams.invoiceMonths) && requestParams.startDate == null) {
      return;
    }
    updateSearchParams({
      filterBy: requestParams.filterBy,
      startDate: requestParams.startDate,
      endDate: requestParams.endDate,
      [INVOICE_MONTHS_FILTER]: null,
    });
  }, [requestParams]);

  const applyFilter = ({ startDate: msStartDate, endDate: msEndDate, invoiceMonths, periodType }) => {
    if (periodType === COST_PERIOD_BILLING || invoiceMonths?.length) {
      const params = {
        filterBy: requestParams.filterBy,
        invoiceMonths: invoiceMonths || [],
      };
      setRequestParams(params);
      return;
    }
    const params = {
      filterBy: requestParams.filterBy,
      startDate: msStartDate,
      endDate: msEndDate,
    };
    setRequestParams(params);
  };

  const updateFilter = (newFilterBy) => {
    if (newFilterBy !== requestParams.filterBy) {
      const params = { ...requestParams, filterBy: newFilterBy };
      setRequestParams(params);
    }
  };

  return [requestParams, applyFilter, updateFilter];
};
