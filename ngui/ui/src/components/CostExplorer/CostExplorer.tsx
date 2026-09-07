import BusinessIcon from "@mui/icons-material/Business";
import CategoryIcon from "@mui/icons-material/Category";
import CloudIcon from "@mui/icons-material/Cloud";
import PeopleIcon from "@mui/icons-material/People";
import PictureAsPdfIcon from "@mui/icons-material/PictureAsPdf";
import PublicIcon from "@mui/icons-material/Public";
import Box from "@mui/material/Box";
import Grid from "@mui/material/Grid";
import { FormattedMessage, useIntl } from "react-intl";
import { useNavigate } from "react-router-dom";
import ActionBar from "components/ActionBar";
import BarChartLoader from "components/BarChartLoader";
import ButtonSwitch from "components/ButtonSwitch";
import { getBasicRangesSet } from "components/DateRangePicker/defaults";
import ExpensesBreakdownBarChart from "components/ExpensesBreakdown/BarChart";
import ExpensesBreakdownByPeriodWidget from "components/ExpensesBreakdown/BreakdownByPeriodWidget";
import ExpensesBreakdownSummaryCards from "components/ExpensesBreakdown/SummaryCards";
import PageContentWrapper from "components/PageContentWrapper";
import Selector, { Item, ItemContent } from "components/Selector";
import SubTitle from "components/SubTitle";
import Tooltip from "components/Tooltip";
import CostPeriodSelector from "components/CostPeriodSelector";
import { useBreakdownData } from "hooks/useBreakdownData";
import { useVirtualTagExtraBreakdowns } from "hooks/useVirtualTagExtraBreakdowns";
import {
  getResourcesExpensesUrl,
  EXPENSES,
  EXPENSES_BY_CLOUD,
  EXPENSES_BY_VENDOR,
  EXPENSES_BY_POOL,
  EXPENSES_BY_OWNER,
  EXPENSES_MAP,
} from "urls";
import { PDF_ELEMENTS } from "utils/constants";
import { COST_PERIOD_BILLING, COST_PERIOD_DATE } from "utils/costPeriod";
import { SPACING_2 } from "utils/layouts";
import { getSearchParams, stringifySearchParams } from "utils/network";
import { createPdf } from "utils/pdf";
import { sliceByLimitWithEllipsis } from "utils/strings";

const breakdownByButtons = [
  { messageId: "source", link: EXPENSES_BY_CLOUD, icon: <CloudIcon /> },
  { messageId: "vendor", link: EXPENSES_BY_VENDOR, icon: <CategoryIcon /> },
  { messageId: "pool", link: EXPENSES_BY_POOL, icon: <BusinessIcon /> },
  { messageId: "owner", link: EXPENSES_BY_OWNER, icon: <PeopleIcon /> },
  { messageId: "geography", link: EXPENSES_MAP, icon: <PublicIcon /> },
];

const VIRTUAL_TAG_SELECTOR_PLACEHOLDER = "__select_virtual_tag__";

const MAX_ORGANIZATION_NAME_LENGTH = 64;

const CostExplorer = ({
  total,
  breakdown,
  previousTotal,
  organizationName,
  isLoading,
  loadProgress = null,
  onApply,
  startDateTimestamp,
  endDateTimestamp,
  invoiceMonths = [],
  isInScopeOfPageMockup = false,
}) => {
  const navigate = useNavigate();
  const intl = useIntl();
  const virtualTagBreakdowns = useVirtualTagExtraBreakdowns();

  const breakdownData = useBreakdownData(breakdown);

  const goToVirtualTagBreakdown = (value: string) => {
    if (!value || value === VIRTUAL_TAG_SELECTOR_PLACEHOLDER) {
      return;
    }
    navigate(
      `${EXPENSES}?${stringifySearchParams({
        ...getSearchParams(),
        filterBy: value,
      })}`
    );
  };

  const isNameLong = organizationName?.length > MAX_ORGANIZATION_NAME_LENGTH;

  const actionBarData = {
    title: {
      text: (
        <FormattedMessage
          id="expensesOf"
          values={{
            name: (
              <Tooltip title={isNameLong ? organizationName : undefined}>
                <span>
                  {isNameLong ? sliceByLimitWithEllipsis(organizationName, MAX_ORGANIZATION_NAME_LENGTH) : organizationName}
                </span>
              </Tooltip>
            ),
          }}
        />
      ),
      isLoading,
    },
    items: [
      {
        key: "costExplorerPdfDownload",
        icon: <PictureAsPdfIcon fontSize="small" />,
        messageId: "download",
        type: "button",
        isLoading,
        action: () => {
          createPdf([
            { type: PDF_ELEMENTS.markup.initPortrait }, // always first

            {
              type: PDF_ELEMENTS.basics.fileName,
              value: "%orgName%_expenses_breakdown_%dateRange%",
              parameters: {
                orgName: {
                  data: organizationName,
                  type: "string",
                },
                dateRange: {
                  data: PDF_ELEMENTS.costExplorer.dates,
                  type: "object",
                },
              },
            },

            { type: PDF_ELEMENTS.markup.logo },

            { type: PDF_ELEMENTS.basics.H1, value: "expensesOf", parameters: { data: { name: organizationName } } },

            { id: PDF_ELEMENTS.costExplorer.dates }, // find component on page with that id and call its pdf render
            { id: PDF_ELEMENTS.costExplorer.expensesSummary }, // another component
            { id: PDF_ELEMENTS.costExplorer.previousExpensesSummary },

            { id: PDF_ELEMENTS.costExplorer.periodWidgetTitle },
            { type: PDF_ELEMENTS.markup.spacer },
            { id: PDF_ELEMENTS.costExplorer.barChart },

            { type: PDF_ELEMENTS.markup.footer },
          ]);
        },
      },
    ],
  };

  const renderBarChart = (periodType) => {
    if (isLoading) {
      return (
        <Grid item xs={12}>
          <BarChartLoader />
        </Grid>
      );
    }
    const isBreakdownDataEmpty = Object.values(breakdownData.daily).every((dailyData) =>
      dailyData.every((d) => d.expense === 0)
    );
    if (isBreakdownDataEmpty) {
      return null;
    }
    return (
      <Grid item xs={12}>
        <ExpensesBreakdownBarChart
          periodType={periodType}
          breakdownData={breakdownData}
          isLoading={isLoading}
          pdfId={PDF_ELEMENTS.costExplorer.barChart}
          onClick={
            isInScopeOfPageMockup
              ? undefined
              : (bandDetails) => {
                  navigate(
                    getResourcesExpensesUrl({
                      sStartDate: bandDetails.startDate,
                      sEndDate: bandDetails.endDate,
                    })
                  );
                }
          }
        />
      </Grid>
    );
  };

  return (
    <>
      <ActionBar data={actionBarData} />
      <PageContentWrapper>
        <Grid container direction="row" justifyContent="space-between" spacing={SPACING_2}>
          <Grid item>
            <ExpensesBreakdownSummaryCards
              total={total}
              previousTotal={previousTotal}
              isLoading={isLoading}
              pdfIds={{
                totalExpensesForSelectedPeriod: PDF_ELEMENTS.costExplorer.expensesSummary,
                totalExpensesForPreviousPeriod: PDF_ELEMENTS.costExplorer.previousExpensesSummary,
              }}
            />
          </Grid>
          <Grid item>
            <CostPeriodSelector
              periodType={invoiceMonths.length ? COST_PERIOD_BILLING : COST_PERIOD_DATE}
              onPeriodTypeChange={(type) => {
                if (type === COST_PERIOD_DATE) {
                  onApply({
                    startDate: startDateTimestamp,
                    endDate: endDateTimestamp,
                    periodType: type,
                  });
                  return;
                }
                onApply({ invoiceMonths, periodType: type });
              }}
              onApplyDates={(range) => onApply({ ...range, periodType: COST_PERIOD_DATE })}
              initialStartDateValue={startDateTimestamp}
              initialEndDateValue={endDateTimestamp}
              pdfId={PDF_ELEMENTS.costExplorer.dates}
              rangeType="expenses"
              definedRanges={getBasicRangesSet()}
              invoiceMonths={invoiceMonths}
              onInvoiceMonthsChange={(months) =>
                onApply({ invoiceMonths: months, periodType: COST_PERIOD_BILLING })
              }
            />
          </Grid>
          <Grid item xs={12}>
            {loadProgress}
            <ExpensesBreakdownByPeriodWidget
              render={(periodType) => (
                <Grid container spacing={SPACING_2}>
                  {renderBarChart(periodType)}
                  <Grid item xs={12}>
                    <SubTitle align="center">
                      <FormattedMessage id="seeExpensesBreakdownBy" />
                    </SubTitle>
                    <Box display="flex" justifyContent="center" alignItems="center" flexWrap="wrap" gap={1}>
                      <ButtonSwitch buttons={breakdownByButtons} />
                      {virtualTagBreakdowns.length > 0 && (
                        <Selector
                          id="cost-explorer-virtual-tag-selector"
                          labelMessageId="virtualTag"
                          value={VIRTUAL_TAG_SELECTOR_PLACEHOLDER}
                          onChange={goToVirtualTagBreakdown}
                          renderValue={() => intl.formatMessage({ id: "virtualTag" })}
                          sx={{ minWidth: 220 }}
                        >
                          <Item value={VIRTUAL_TAG_SELECTOR_PLACEHOLDER} disabled>
                            <ItemContent>
                              <FormattedMessage id="virtualTag" />
                            </ItemContent>
                          </Item>
                          {virtualTagBreakdowns.map((item) => (
                            <Item key={item.value} value={item.value}>
                              <ItemContent>{item.name}</ItemContent>
                            </Item>
                          ))}
                        </Selector>
                      )}
                    </Box>
                  </Grid>
                </Grid>
              )}
            />
          </Grid>
        </Grid>
      </PageContentWrapper>
    </>
  );
};

export default CostExplorer;
