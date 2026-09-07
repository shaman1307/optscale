import { useState } from "react";
import { Box } from "@mui/material";
import { useTheme } from "@mui/material/styles";
import ActionBar from "components/ActionBar";
import { getBasicRangesSet } from "components/DateRangePicker/defaults";
import Mocked from "components/Mocked";
import PageContentWrapper from "components/PageContentWrapper";
import { RegionExpensesMocked } from "components/RegionExpenses";
import TabsWrapper from "components/TabsWrapper";
import { TrafficExpensesMocked } from "components/TrafficExpenses";
import TrafficExpensesContainer from "components/TrafficExpensesContainer";
import CostPeriodSelector from "components/CostPeriodSelector";
import RegionExpensesContainer from "containers/RegionExpensesContainer";
import { useReactiveDefaultDateRange } from "hooks/useReactiveDefaultDateRange";
import { useReactiveSearchParam } from "hooks/useReactiveSearchParam";
import { DATE_RANGE_TYPE, EXPENSES_MAP_TYPES, INVOICE_MONTHS_FILTER } from "utils/constants";
import { COST_PERIOD_BILLING, COST_PERIOD_DATE, normalizeInvoiceMonths } from "utils/costPeriod";
import { SPACING_2 } from "utils/layouts";
import { getSearchParams, updateSearchParams } from "utils/network";

const actionBarDefinition = {
  title: {
    messageId: "costMapTitle",
  },
};

const ExpensesMap = () => {
  const theme = useTheme();
  const tabs = [
    {
      title: EXPENSES_MAP_TYPES.REGION,
      dataTestId: `tab_${EXPENSES_MAP_TYPES.REGION}`,
      node: (
        <Mocked mock={<RegionExpensesMocked />}>
          <RegionExpensesContainer />
        </Mocked>
      ),
    },
    {
      title: EXPENSES_MAP_TYPES.TRAFFIC,
      dataTestId: `tab_${EXPENSES_MAP_TYPES.TRAFFI}`,
      node: (
        <Mocked mock={<TrafficExpensesMocked />}>
          <TrafficExpensesContainer />
        </Mocked>
      ),
    },
  ];

  // dates query handlers
  const [startDateTimestamp, endDateTimestamp] = useReactiveDefaultDateRange(DATE_RANGE_TYPE.EXPENSES);
  const [invoiceMonths, setInvoiceMonths] = useState(() =>
    normalizeInvoiceMonths(getSearchParams()[INVOICE_MONTHS_FILTER])
  );
  const [periodType, setPeriodType] = useState(() =>
    invoiceMonths.length ? COST_PERIOD_BILLING : COST_PERIOD_DATE
  );
  const mapType = useReactiveSearchParam("type") || EXPENSES_MAP_TYPES.REGION;
  const showBillingToggle = mapType !== EXPENSES_MAP_TYPES.TRAFFIC;

  const applyDates = ({ startDate, endDate }) => {
    setPeriodType(COST_PERIOD_DATE);
    setInvoiceMonths([]);
    updateSearchParams({
      startDate,
      endDate,
      [INVOICE_MONTHS_FILTER]: null,
    });
  };

  const applyInvoiceMonths = (months) => {
    setPeriodType(COST_PERIOD_BILLING);
    setInvoiceMonths(months);
    if (!months?.length) {
      return;
    }
    updateSearchParams({
      [INVOICE_MONTHS_FILTER]: months,
      startDate: null,
      endDate: null,
    });
  };

  return (
    <>
      <ActionBar data={actionBarDefinition} />
      <PageContentWrapper>
        <TabsWrapper
          tabsProps={{
            name: "expensesMapsTab",
            queryTabName: "type",
            tabs,
            defaultTab: EXPENSES_MAP_TYPES.REGION,
          }}
          headerSx={{
            display: "flex",
            justifyContent: "space-between",
            flexDirection: {
              sm: "row",
              xs: "column",
            },
          }}
          headerAdornment={
            <Box display="flex" alignItems="center" sx={{ py: { xs: theme.spacing(SPACING_2), sm: 0 } }}>
              <CostPeriodSelector
                periodType={periodType}
                onPeriodTypeChange={(nextType) => {
                  if (nextType === COST_PERIOD_DATE) {
                    applyDates({ startDate: startDateTimestamp, endDate: endDateTimestamp });
                    return;
                  }
                  setPeriodType(COST_PERIOD_BILLING);
                }}
                onApplyDates={applyDates}
                initialStartDateValue={startDateTimestamp}
                initialEndDateValue={endDateTimestamp}
                rangeType={DATE_RANGE_TYPE.EXPENSES}
                definedRanges={getBasicRangesSet()}
                invoiceMonths={invoiceMonths}
                onInvoiceMonthsChange={applyInvoiceMonths}
                forceDateRange={!showBillingToggle}
              />
            </Box>
          }
        />
      </PageContentWrapper>
    </>
  );
};

export default ExpensesMap;
