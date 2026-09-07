import { useEffect, useState } from "react";
import Grid from "@mui/material/Grid";
import ButtonGroup from "components/ButtonGroup";
import { getBasicRangesSet, getCustomRange } from "components/DateRangePicker/defaults";
import CostPeriodSelector from "components/CostPeriodSelector";
import ResourcePaidNetworkTrafficContainer from "containers/ResourcePaidNetworkTrafficContainer";
import ResourceRawExpensesContainer from "containers/ResourceRawExpensesContainer";
import { useResourceDetailsDefaultDateRange } from "hooks/useResourceDetailsDefaultDateRange";
import { RESOURCE_PAGE_EXPENSES_TABS, DATE_RANGE_FILTERS, DATE_RANGE_TYPE, INVOICE_MONTHS_FILTER } from "utils/constants";
import { COST_PERIOD_BILLING, COST_PERIOD_DATE, normalizeInvoiceMonths } from "utils/costPeriod";
import { millisecondsToSeconds, performDateTimeFunction, startOfDay, endOfDay, secondsToMilliseconds } from "utils/datetime";
import { SPACING_2 } from "utils/layouts";
import { getSearchParams, updateSearchParams } from "utils/network";

const useActiveExpensesMode = ({ modes, defaultMode }) => {
  const { expensesMode } = getSearchParams();
  const [activeExpensesMode, setActiveExpensesMode] = useState(modes.includes(expensesMode) ? expensesMode : defaultMode);

  useEffect(() => {
    updateSearchParams({ expensesMode: activeExpensesMode });
  }, [activeExpensesMode]);

  return [activeExpensesMode, setActiveExpensesMode];
};

const ExpensesModeButtonGroup = ({ activeExpensesMode, onClick, hasNetworkTrafficExpenses }) => {
  const buttonsGroup = [
    {
      id: RESOURCE_PAGE_EXPENSES_TABS.GROUPED,
      messageId: "grouped",
      action: () => onClick(RESOURCE_PAGE_EXPENSES_TABS.GROUPED),
      dataTestId: "btn_grouped",
    },
    {
      id: RESOURCE_PAGE_EXPENSES_TABS.DETAILED,
      messageId: "detailed",
      action: () => onClick(RESOURCE_PAGE_EXPENSES_TABS.DETAILED),
      dataTestId: "btn_detailed",
    },
    ...(hasNetworkTrafficExpenses
      ? [
          {
            id: RESOURCE_PAGE_EXPENSES_TABS.PAID_NETWORK_TRAFFIC,
            messageId: "paidNetworkTraffic",
            action: () => onClick(RESOURCE_PAGE_EXPENSES_TABS.PAID_NETWORK_TRAFFIC),
            dataTestId: "btn_paid_network_traffic",
          },
        ]
      : []),
  ];

  return (
    <ButtonGroup
      buttons={buttonsGroup}
      activeButtonIndex={buttonsGroup.findIndex((button) => button.id === activeExpensesMode)}
    />
  );
};

const ResourceExpenses = ({ resourceId, firstSeen, lastSeen, hasNetworkTrafficExpenses = false }) => {
  const [startDate, endDate] = useResourceDetailsDefaultDateRange({
    lastSeen,
    firstSeen,
  });

  const [activeExpensesMode, setActiveExpensesMode] = useActiveExpensesMode({
    modes: Object.values(RESOURCE_PAGE_EXPENSES_TABS),
    defaultMode: RESOURCE_PAGE_EXPENSES_TABS.GROUPED,
  });

  const [requestParams, setRequestParams] = useState(() => {
    const invoiceMonths = normalizeInvoiceMonths(getSearchParams()[INVOICE_MONTHS_FILTER]);
    return {
      startDate,
      endDate,
      invoiceMonths,
      periodType: invoiceMonths.length ? COST_PERIOD_BILLING : COST_PERIOD_DATE,
    };
  });

  const isTraffic = activeExpensesMode === RESOURCE_PAGE_EXPENSES_TABS.PAID_NETWORK_TRAFFIC;

  useEffect(() => {
    if (requestParams.periodType === COST_PERIOD_BILLING && requestParams.invoiceMonths?.length) {
      updateSearchParams({
        [INVOICE_MONTHS_FILTER]: requestParams.invoiceMonths,
        startDate: null,
        endDate: null,
      });
      return;
    }
    updateSearchParams({
      startDate: requestParams.startDate,
      endDate: requestParams.endDate,
      [INVOICE_MONTHS_FILTER]: null,
    });
  }, [requestParams.startDate, requestParams.endDate, requestParams.invoiceMonths, requestParams.periodType]);

  const firstSeenStartOfDay = millisecondsToSeconds(
    performDateTimeFunction(startOfDay, true, secondsToMilliseconds(firstSeen))
  );
  const lastSeenEndOfDay = millisecondsToSeconds(performDateTimeFunction(endOfDay, true, secondsToMilliseconds(lastSeen)));

  const applyFilter = ({ startDate: newStartDate, endDate: newEndDate, invoiceMonths, periodType }) => {
    if (periodType === COST_PERIOD_BILLING || invoiceMonths?.length) {
      setRequestParams({
        ...requestParams,
        invoiceMonths: invoiceMonths || [],
        periodType: COST_PERIOD_BILLING,
      });
      return;
    }
    setRequestParams({
      ...requestParams,
      startDate: newStartDate,
      endDate: newEndDate,
      invoiceMonths: [],
      periodType: COST_PERIOD_DATE,
    });
  };

  return (
    <Grid container spacing={SPACING_2} alignItems="center" justifyContent="space-between">
      <Grid item>
        <ExpensesModeButtonGroup
          onClick={(id) => setActiveExpensesMode(id)}
          activeExpensesMode={activeExpensesMode}
          hasNetworkTrafficExpenses={hasNetworkTrafficExpenses}
        />
      </Grid>
      <Grid item>
        <CostPeriodSelector
          periodType={requestParams.periodType}
          onPeriodTypeChange={(nextType) => {
            if (nextType === COST_PERIOD_DATE) {
              applyFilter({
                startDate: requestParams.startDate,
                endDate: requestParams.endDate,
                periodType: COST_PERIOD_DATE,
              });
              return;
            }
            applyFilter({ periodType: COST_PERIOD_BILLING, invoiceMonths: requestParams.invoiceMonths });
          }}
          onApplyDates={applyFilter}
          initialStartDateValue={requestParams.startDate}
          initialEndDateValue={requestParams.endDate}
          definedRanges={[
            getCustomRange({
              messageId: DATE_RANGE_FILTERS.ALL,
              startDate: firstSeenStartOfDay || millisecondsToSeconds(+new Date()),
              endDate: lastSeenEndOfDay || millisecondsToSeconds(+new Date()),
              dataTestId: "btn_all",
            }),
            ...getBasicRangesSet(),
          ]}
          rangeType={DATE_RANGE_TYPE.RESOURCES}
          minDate={firstSeenStartOfDay}
          maxDate={lastSeenEndOfDay}
          invoiceMonths={requestParams.invoiceMonths}
          onInvoiceMonthsChange={(months) => applyFilter({ invoiceMonths: months, periodType: COST_PERIOD_BILLING })}
          forceDateRange={isTraffic}
        />
      </Grid>
      <Grid item xs={12}>
        {[RESOURCE_PAGE_EXPENSES_TABS.GROUPED, RESOURCE_PAGE_EXPENSES_TABS.DETAILED].includes(activeExpensesMode) && (
          <ResourceRawExpensesContainer
            resourceId={resourceId}
            startDate={requestParams.startDate}
            endDate={requestParams.endDate}
            invoiceMonths={requestParams.invoiceMonths}
            periodType={requestParams.periodType}
            expensesMode={activeExpensesMode}
          />
        )}
        {[RESOURCE_PAGE_EXPENSES_TABS.PAID_NETWORK_TRAFFIC].includes(activeExpensesMode) && (
          <ResourcePaidNetworkTrafficContainer
            resourceId={resourceId}
            startDate={requestParams.startDate}
            endDate={requestParams.endDate}
          />
        )}
      </Grid>
    </Grid>
  );
};

export default ResourceExpenses;
