import { Box } from "@mui/material";
import Grid from "@mui/material/Grid";
import { useTheme } from "@mui/material/styles";
import { FormattedMessage } from "react-intl";
import { useNavigate } from "react-router-dom";
import BarChartLoader from "components/BarChartLoader";
import { getBasicRangesSet } from "components/DateRangePicker/defaults";
import PageContentWrapper from "components/PageContentWrapper";
import PieChartLoader from "components/PieChartLoader";
import CostPeriodSelector from "components/CostPeriodSelector";
import { useBreakdownData } from "hooks/useBreakdownData";
import {
  DAILY_EXPENSES_BREAKDOWN_BY_PARAMETER_NAME,
  getResourcesExpensesUrl,
  getOwnerExpensesUrl,
  getCloudExpensesUrl,
  getPoolExpensesUrl,
} from "urls";
import { getColorsMapByIds } from "utils/charts";
import {
  COST_EXPLORER,
  OWNER_DETAILS,
  OWNER_ID_FILTER,
  CLOUD_DETAILS,
  CLOUD_ACCOUNT_ID_FILTER,
  POOL_DETAILS,
  RESOURCE_TYPE_FILTER,
  POOL_ID_FILTER,
  EXPENSES_FILTERBY_TYPES,
  REGION_FILTER,
  SERVICE_NAME_FILTER,
  EMPTY_UUID,
  KUBERNETES_CNR,
  K8S_NODE_FILTER,
  K8S_NAMESPACE_FILTER,
  OPTSCALE_RESOURCE_TYPES,
  INVOICE_MONTHS_FILTER,
  VIRTUAL_TAG_FILTER,
  VENDOR_FILTER,
} from "utils/constants";
import { COST_PERIOD_BILLING, COST_PERIOD_DATE } from "utils/costPeriod";
import { getVirtualTagKeyFromFilterBy, isVirtualTagBreakdown } from "utils/virtualTagBreakdown";
import ExpensesBreakdownActionBar from "./ActionBar";
import ExpensesBreakdownBarChart from "./BarChart";
import ExpensesBreakdownBreakdownByButtonsGroup from "./BreakdownByButtonsGroup";
import ExpensesBreakdownByPeriodWidget from "./BreakdownByPeriodWidget";
import ExpensesBreakdownLayoutWrapper from "./LayoutWrapper";
import ExpensesBreakdownPieChart from "./PieChart";
import ExpensesBreakdownDataSourceFilter from "./DataSourceFilter";
import ExpensesBreakdownRegionFilter from "./RegionFilter";
import ExpensesBreakdownSummaryCards from "./SummaryCards";
import ExpensesBreakdownTableWidget from "./TableWidget";
import ExpensesBreakdownVendorFilter from "./VendorFilter";

const ExpensesBreakdown = ({
  entityId,
  filterBy,
  type,
  breakdown,
  total,
  previousTotal,
  filteredBreakdown,
  startDateTimestamp,
  endDateTimestamp,
  invoiceMonths = [],
  isLoading,
  loadProgress = null,
  onApply,
  updateFilter,
  name,
  dataSourceType,
  isInScopeOfPageMockup = false,
  appliedRegionValues = [],
  appliedCloudAccountValues = [],
  appliedVendorValues = [],
  showRegionFilter = false,
  regionFilter,
  regionFilterValues,
  onRegionFilterChange,
  cloudAccountFilter,
  cloudAccountFilterValues,
  onCloudAccountFilterChange,
  vendorFilter,
  onVendorFilterChange,
  isRegionFilterLoading = false,
}) => {
  const navigate = useNavigate();
  const theme = useTheme();

  const breakdownData = useBreakdownData(breakdown);

  const colorsMap = getColorsMapByIds({
    data: filteredBreakdown,
    colors: theme.palette.chart,
  });

  const getEntityExpensesUrl = (targetEntityId, targetEntityType) => {
    if (targetEntityId === EMPTY_UUID || isInScopeOfPageMockup) {
      return undefined;
    }
    return {
      [EXPENSES_FILTERBY_TYPES.EMPLOYEE]: `${getOwnerExpensesUrl(targetEntityId)}?filterBy=${EXPENSES_FILTERBY_TYPES.POOL}`,
      [EXPENSES_FILTERBY_TYPES.CLOUD]: `${getCloudExpensesUrl(targetEntityId)}?filterBy=${
        targetEntityType === KUBERNETES_CNR ? EXPENSES_FILTERBY_TYPES.NODE : EXPENSES_FILTERBY_TYPES.SERVICE
      }`,
      [EXPENSES_FILTERBY_TYPES.POOL]:
        type === POOL_DETAILS
          ? entityId !== targetEntityId && `${getPoolExpensesUrl(targetEntityId)}?filterBy=${EXPENSES_FILTERBY_TYPES.POOL}`
          : `${getPoolExpensesUrl(targetEntityId)}?filterBy=${EXPENSES_FILTERBY_TYPES.POOL}`,
    }[filterBy];
  };

  const getFilterByEntity = (isTableWrapper = false) => {
    const regionParams = appliedRegionValues.length ? { [REGION_FILTER]: appliedRegionValues } : {};
    const cloudAccountParams = appliedCloudAccountValues.length
      ? { [CLOUD_ACCOUNT_ID_FILTER]: appliedCloudAccountValues }
      : {};
    const vendorParams = appliedVendorValues.length ? { [VENDOR_FILTER]: appliedVendorValues } : {};
    const virtualTagContext = isVirtualTagBreakdown(filterBy)
      ? {
          [DAILY_EXPENSES_BREAKDOWN_BY_PARAMETER_NAME]: filterBy,
          ...regionParams,
          ...cloudAccountParams,
          ...vendorParams,
        }
      : {};
    return {
      [OWNER_DETAILS]: {
        [OWNER_ID_FILTER]: entityId,
      },
      [CLOUD_DETAILS]: {
        [CLOUD_ACCOUNT_ID_FILTER]: entityId,
      },
      [POOL_DETAILS]: isTableWrapper
        ? {
            [POOL_ID_FILTER]: entityId,
          }
        : {},
      [COST_EXPLORER]: virtualTagContext,
    }[type];
  };

  const getVirtualTagComputedParams = (details) => {
    const tagKey = getVirtualTagKeyFromFilterBy(filterBy);
    const valueId = details?.id;
    const isUnset = valueId == null || valueId === "" || valueId === "(not set)";
    const virtualTagParam = `${VIRTUAL_TAG_FILTER}=${isUnset ? EMPTY_UUID : `${tagKey}:${valueId}`}`;
    const regionParams = appliedRegionValues.map((region) => `${REGION_FILTER}=${region}`);
    const cloudAccountParams = appliedCloudAccountValues.map(
      (cloudAccountId) => `${CLOUD_ACCOUNT_ID_FILTER}=${cloudAccountId}`
    );
    const vendorParams = appliedVendorValues.map((vendor) => `${VENDOR_FILTER}=${vendor}`);
    return [
      virtualTagParam,
      `${DAILY_EXPENSES_BREAKDOWN_BY_PARAMETER_NAME}=${filterBy}`,
      ...regionParams,
      ...cloudAccountParams,
      ...vendorParams,
    ].join("&");
  };

  const getComputedParams = (details) => {
    if (isVirtualTagBreakdown(filterBy)) {
      return getVirtualTagComputedParams(details);
    }
    return {
      [EXPENSES_FILTERBY_TYPES.POOL]: `${POOL_ID_FILTER}=${details.id}`,
      [EXPENSES_FILTERBY_TYPES.CLOUD]:
        type === POOL_DETAILS
          ? `${CLOUD_ACCOUNT_ID_FILTER}=${details.id}&${POOL_ID_FILTER}=${entityId}`
          : `${CLOUD_ACCOUNT_ID_FILTER}=${details.id}`,
      [EXPENSES_FILTERBY_TYPES.VENDOR]: (details.cloud_account_ids || [])
        .map((cloudAccountId) => `${CLOUD_ACCOUNT_ID_FILTER}=${cloudAccountId}`)
        .concat(type === POOL_DETAILS ? [`${POOL_ID_FILTER}=${entityId}`] : [])
        .join("&"),
      [EXPENSES_FILTERBY_TYPES.EMPLOYEE]:
        type === POOL_DETAILS
          ? `${OWNER_ID_FILTER}=${details.id}&${POOL_ID_FILTER}=${entityId}`
          : `${OWNER_ID_FILTER}=${details.id}`,
      [EXPENSES_FILTERBY_TYPES.SERVICE]: `${SERVICE_NAME_FILTER}=${details.id}`,
      [EXPENSES_FILTERBY_TYPES.REGION]: `${REGION_FILTER}=${details.id}`,
      // Expenses are tracked for regular resources only.
      // When/if a different type is added to calculations (e.g. 'cluster'), the API will have to return it.
      [EXPENSES_FILTERBY_TYPES.RESOURCE_TYPE]: `${RESOURCE_TYPE_FILTER}=${details.name}:${OPTSCALE_RESOURCE_TYPES.REGULAR}`,
      [EXPENSES_FILTERBY_TYPES.NODE]: `${K8S_NODE_FILTER}=${details.id}`,
      [EXPENSES_FILTERBY_TYPES.NAMESPACE]: `${K8S_NAMESPACE_FILTER}=${details.id}`,
    }[filterBy];
  };

  const renderHeading = () => (
    <>
      <Grid item>
        <ExpensesBreakdownSummaryCards total={total} previousTotal={previousTotal} isLoading={isLoading} />
      </Grid>
      <Grid item>
        <CostPeriodSelector
          periodType={invoiceMonths.length ? COST_PERIOD_BILLING : COST_PERIOD_DATE}
          onPeriodTypeChange={(nextType) => {
            if (nextType === COST_PERIOD_DATE) {
              onApply({
                startDate: startDateTimestamp,
                endDate: endDateTimestamp,
                periodType: nextType,
              });
              return;
            }
            onApply({ invoiceMonths, periodType: nextType });
          }}
          onApplyDates={(range) => onApply({ ...range, periodType: COST_PERIOD_DATE })}
          initialStartDateValue={startDateTimestamp}
          initialEndDateValue={endDateTimestamp}
          rangeType="expenses"
          definedRanges={getBasicRangesSet()}
          invoiceMonths={invoiceMonths}
          onInvoiceMonthsChange={(months) =>
            onApply({ invoiceMonths: months, periodType: COST_PERIOD_BILLING })
          }
        />
      </Grid>
      {loadProgress && (
        <Grid item xs={12}>
          {loadProgress}
        </Grid>
      )}
      {type !== COST_EXPLORER && (
        <Grid item xs={12}>
          <ExpensesBreakdownBreakdownByButtonsGroup
            filterBy={filterBy}
            type={type}
            onClick={updateFilter}
            dataSourceType={dataSourceType}
          />
        </Grid>
      )}
      {showRegionFilter && (
        <Grid item xs={12}>
          <Box display="flex" flexWrap="wrap" gap={1} alignItems="center">
            <ExpensesBreakdownRegionFilter
              regionFilterValues={regionFilterValues}
              appliedFilter={regionFilter}
              onChange={onRegionFilterChange}
              isLoading={isRegionFilterLoading}
            />
            <ExpensesBreakdownDataSourceFilter
              cloudAccountFilterValues={cloudAccountFilterValues}
              appliedFilter={cloudAccountFilter}
              onChange={onCloudAccountFilterChange}
              isLoading={isRegionFilterLoading}
            />
            <ExpensesBreakdownVendorFilter
              cloudAccountFilterValues={cloudAccountFilterValues}
              appliedFilter={vendorFilter}
              onChange={onVendorFilterChange}
              isLoading={isRegionFilterLoading}
            />
          </Box>
        </Grid>
      )}
    </>
  );

  const renderBarChartWidget = () => {
    if (isLoading) {
      return <ExpensesBreakdownByPeriodWidget render={() => <BarChartLoader />} />;
    }
    const isBreakdownDataEmpty = Object.values(breakdownData.daily).every((dailyData) =>
      dailyData.every((d) => d.expense === 0)
    );
    if (isBreakdownDataEmpty) {
      return null;
    }
    return (
      <ExpensesBreakdownByPeriodWidget
        render={(periodType) => (
          <ExpensesBreakdownBarChart
            periodType={periodType}
            breakdownData={breakdownData}
            colorsMap={colorsMap}
            fieldTooltipText={["id"]}
            filterBy={filterBy}
            onClick={
              isInScopeOfPageMockup
                ? undefined
                : (bandDetails) => {
                    navigate(
                      getResourcesExpensesUrl({
                        ...getFilterByEntity(),
                        computedParams: getComputedParams(bandDetails),
                        sStartDate: bandDetails.startDate,
                        sEndDate: bandDetails.endDate,
                      })
                    );
                  }
            }
          />
        )}
      />
    );
  };

  const PieChartHeader = () => (
    <Box justifyContent="center" display="flex">
      <FormattedMessage id="expenses" />
    </Box>
  );

  const renderPieChartWidget = () => {
    if (isLoading) {
      return (
        <>
          <PieChartHeader />
          <PieChartLoader height={40} />
        </>
      );
    }
    if (filteredBreakdown.length <= 1) {
      return null;
    }
    return (
      <>
        <PieChartHeader />
        <ExpensesBreakdownPieChart
          filteredBreakdown={filteredBreakdown}
          filterBy={filterBy}
          colorsMap={colorsMap}
          onClick={
            isInScopeOfPageMockup
              ? undefined
              : (node) => {
                  const { data: { details: { link = "" } = {} } = {} } = node;
                  if (link) {
                    navigate(link);
                  }
                }
          }
          getCustomDetails={({ id, type: sectionEntityType }) => ({
            link: getEntityExpensesUrl(id, sectionEntityType),
          })}
          getShouldApplyHoverStyles={(node) => {
            const { data: { details: { link = "" } = {} } = {} } = node;
            return !!link;
          }}
        />
      </>
    );
  };

  const renderTableWidget = () => (
    <ExpensesBreakdownTableWidget
      filteredBreakdown={filteredBreakdown}
      colorsMap={colorsMap}
      total={total}
      filterBy={filterBy}
      isLoading={isLoading}
      getEntityExpensesUrl={getEntityExpensesUrl}
      startDateTimestamp={startDateTimestamp}
      endDateTimestamp={endDateTimestamp}
      onTitleButtonClick={() =>
        isInScopeOfPageMockup
          ? undefined
          : navigate(
              getResourcesExpensesUrl({
                ...getFilterByEntity(true),
                ...(invoiceMonths.length
                  ? { [INVOICE_MONTHS_FILTER]: invoiceMonths }
                  : { sStartDate: startDateTimestamp, sEndDate: endDateTimestamp }),
              })
            )
      }
      onRowActionClick={(rowData) =>
        isInScopeOfPageMockup
          ? undefined
          : navigate(
              getResourcesExpensesUrl({
                ...getFilterByEntity(),
                computedParams: getComputedParams(rowData),
                ...(invoiceMonths.length
                  ? { [INVOICE_MONTHS_FILTER]: invoiceMonths }
                  : { sStartDate: startDateTimestamp, sEndDate: endDateTimestamp }),
              })
            )
      }
    />
  );

  return (
    <>
      <ExpensesBreakdownActionBar expensesBreakdownType={type} filterBy={filterBy} name={name} isLoading={isLoading} />
      <PageContentWrapper>
        <ExpensesBreakdownLayoutWrapper
          top={renderHeading()}
          center={{
            left: renderBarChartWidget(),
            right: renderPieChartWidget(),
          }}
          bottom={renderTableWidget()}
        />
      </PageContentWrapper>
    </>
  );
};

export default ExpensesBreakdown;
