import { useMemo, useState } from "react";
import { Typography } from "@mui/material";
import { FormattedMessage } from "react-intl";
import ExpensesBreakdown from "components/ExpensesBreakdown";
import { FILTER_CONFIGS } from "components/Resources/filterConfigs";
import { useAvailableFiltersQuery } from "graphql/__generated__/hooks/restapi";
import { useAsymptoticLoadPercent } from "hooks/useAsymptoticLoadPercent";
import { useOrganizationInfo } from "hooks/useOrganizationInfo";
import { useVirtualTagExtraBreakdowns } from "hooks/useVirtualTagExtraBreakdowns";
import DailyExpensesBreakdownByService from "services/DailyExpensesBreakdownByService";
import { CLOUD_ACCOUNT_ID_FILTER, COST_EXPLORER, REGION_FILTER, VENDOR_FILTER } from "utils/constants";
import { toExpensePeriodApiParams } from "utils/costPeriod";
import { getDataSourceVendor } from "utils/dataSourceVendors";
import { getSearchParams, updateSearchParams } from "utils/network";
import { getVirtualTagKeyFromFilterBy, mapBreakdownExpensesToFormatted } from "utils/virtualTagBreakdown";

const VIRTUAL_TAG_BREAKDOWN_EXPECTED_LOAD_SEC = 57;

const getVendorValuesFromSearchParams = () => {
  const { [VENDOR_FILTER]: value } = getSearchParams();
  if ([null, undefined].includes(value)) {
    return [];
  }
  return Array.isArray(value) ? value : [value];
};

const getCloudAccountIdsForVendors = (cloudAccounts, vendorIds) => {
  if (!vendorIds?.length) {
    return null;
  }
  const selected = new Set(vendorIds);
  return (cloudAccounts || [])
    .filter((account) => account?.id && selected.has(getDataSourceVendor(account.type).id))
    .map((account) => account.id);
};

const resolveCloudAccountIds = (explicitIds, vendorIds, cloudAccounts) => {
  const fromVendors = getCloudAccountIdsForVendors(cloudAccounts, vendorIds);
  if (explicitIds?.length && fromVendors) {
    const vendorSet = new Set(fromVendors);
    return explicitIds.filter((id) => vendorSet.has(id));
  }
  if (explicitIds?.length) {
    return explicitIds;
  }
  if (fromVendors?.length) {
    return fromVendors;
  }
  return undefined;
};

const VirtualTagExpensesBreakdownContainer = ({
  filterBy,
  startDateTimestamp,
  endDateTimestamp,
  invoiceMonths = [],
  onApply,
  skipRequest: skipRequestProp = false,
}) => {
  const { organizationId } = useOrganizationInfo();
  const extraBreakdowns = useVirtualTagExtraBreakdowns();
  const { useGet } = DailyExpensesBreakdownByService();

  const virtualTagName =
    extraBreakdowns.find((item) => item.value === filterBy)?.name || getVirtualTagKeyFromFilterBy(filterBy);

  const [regionFilter, setRegionFilter] = useState(() => FILTER_CONFIGS.region.getValuesFromSearchParams());
  const [cloudAccountFilter, setCloudAccountFilter] = useState(() =>
    FILTER_CONFIGS.cloudAccountId.getValuesFromSearchParams()
  );
  const [vendorFilter, setVendorFilter] = useState(() => ({
    values: getVendorValuesFromSearchParams(),
  }));

  const periodParams = useMemo(
    () =>
      toExpensePeriodApiParams({
        startDate: startDateTimestamp,
        endDate: endDateTimestamp,
        invoiceMonths,
      }),
    [startDateTimestamp, endDateTimestamp, invoiceMonths]
  );

  const { data: availableFiltersData, loading: isFiltersLoading } = useAvailableFiltersQuery({
    skip: !organizationId || skipRequestProp,
    variables: {
      organizationId,
      params: {
        ...periodParams,
        facets: "core",
      },
    },
  });

  const cloudAccounts = availableFiltersData?.availableFilters?.cloud_account;

  const resolvedCloudAccountIds = useMemo(
    () =>
      resolveCloudAccountIds(cloudAccountFilter.values, vendorFilter.values, cloudAccounts) || [],
    [cloudAccountFilter.values, vendorFilter.values, cloudAccounts]
  );

  const requestParams = useMemo(() => {
    const regionApi = FILTER_CONFIGS.region.transformers.toApi(regionFilter);
    return {
      ...periodParams,
      breakdown_by: filterBy,
      ...(regionApi.region?.length ? regionApi : {}),
      ...(resolvedCloudAccountIds.length ? { cloud_account_id: resolvedCloudAccountIds } : {}),
    };
  }, [filterBy, periodParams, regionFilter, resolvedCloudAccountIds]);

  const skipForVendorScope = Boolean(vendorFilter.values?.length) && cloudAccounts == null;
  const skipRequest = skipRequestProp || skipForVendorScope;

  const { isLoading, data = {} } = useGet(skipRequest ? null : requestParams);
  const { breakdown, filteredBreakdown, total, previousTotal } = mapBreakdownExpensesToFormatted(data);
  const isBreakdownLoading = isLoading || skipForVendorScope;

  const loadPercent = useAsymptoticLoadPercent(
    isBreakdownLoading,
    VIRTUAL_TAG_BREAKDOWN_EXPECTED_LOAD_SEC,
    requestParams
  );

  const onRegionFilterChange = (nextFilter) => {
    const nextValue = nextFilter || FILTER_CONFIGS.region.getDefaultValue();
    updateSearchParams({
      [REGION_FILTER]: nextValue.values?.length ? nextValue.values : null,
    });
    setRegionFilter(nextValue);
  };

  const onCloudAccountFilterChange = (nextFilter) => {
    const nextValue = nextFilter || FILTER_CONFIGS.cloudAccountId.getDefaultValue();
    updateSearchParams({
      [CLOUD_ACCOUNT_ID_FILTER]: nextValue.values?.length ? nextValue.values : null,
    });
    setCloudAccountFilter(nextValue);
  };

  const onVendorFilterChange = (nextFilter) => {
    const nextValue = nextFilter || { values: [] };
    updateSearchParams({
      [VENDOR_FILTER]: nextValue.values?.length ? nextValue.values : null,
    });
    setVendorFilter(nextValue);
  };

  const loadProgress =
    isBreakdownLoading && loadPercent < 100 ? (
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }} data-test-id="cost_explorer_load_progress">
        <FormattedMessage id="resourcesPageLoadingProgress" values={{ value: loadPercent }} />
      </Typography>
    ) : null;

  return (
    <ExpensesBreakdown
      filterBy={filterBy}
      type={COST_EXPLORER}
      breakdown={breakdown}
      total={total}
      previousTotal={previousTotal}
      filteredBreakdown={filteredBreakdown}
      startDateTimestamp={startDateTimestamp}
      endDateTimestamp={endDateTimestamp}
      invoiceMonths={invoiceMonths}
      isLoading={isBreakdownLoading}
      loadProgress={loadProgress}
      onApply={onApply}
      name={virtualTagName}
      appliedRegionValues={regionFilter.values}
      appliedCloudAccountValues={
        resolvedCloudAccountIds.length ? resolvedCloudAccountIds : cloudAccountFilter.values
      }
      appliedVendorValues={vendorFilter.values}
      showRegionFilter={!isBreakdownLoading}
      regionFilter={regionFilter}
      regionFilterValues={availableFiltersData?.availableFilters?.region}
      onRegionFilterChange={onRegionFilterChange}
      cloudAccountFilter={cloudAccountFilter}
      cloudAccountFilterValues={cloudAccounts}
      onCloudAccountFilterChange={onCloudAccountFilterChange}
      vendorFilter={vendorFilter}
      onVendorFilterChange={onVendorFilterChange}
      isRegionFilterLoading={isFiltersLoading}
    />
  );
};

export default VirtualTagExpensesBreakdownContainer;
