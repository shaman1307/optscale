import { useEffect, useMemo, useState } from "react";
import ExpandLessIcon from "@mui/icons-material/ExpandLess";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import RestartAltOutlinedIcon from "@mui/icons-material/RestartAltOutlined";
import { Badge, Box } from "@mui/material";
import Button from "@mui/material/Button";
import { FormattedMessage, useIntl } from "react-intl";
import { RangeFilter, SelectionFilter, SuggestionFilter } from "components/FilterComponents";
import { FILTER_CONFIGS } from "components/Resources/filterConfigs";
import { useAvailableFiltersQuery } from "graphql/__generated__/hooks/restapi";
import { useCurrentEmployee } from "hooks/coreData/useCurrentEmployee";
import { useHasKubernetesDataSource } from "hooks/useHasKubernetesDataSource";
import { useOrganizationInfo } from "hooks/useOrganizationInfo";
import { isEmptyArray } from "utils/arrays";
import { K8S_RESOURCE_FILTER_IDS } from "utils/constants";
import { COST_PERIOD_BILLING } from "utils/costPeriod";
import { endOfDay, moveDateFromUTC, startOfDay } from "utils/datetime";

const getSelectionFilterProps = ({ config, onChange, appliedFilters, data, isLoading, onOpen }) => ({
  items: config.transformers.getItems(data),
  label: config.label,
  buttonIcon: config.icon,
  renderItem: config.renderItem,
  renderSelectedItem: config.renderSelectedItem,
  searchPredicate: config.searchPredicate,
  onChange: onChange(config.id),
  appliedItems: appliedFilters[config.id],
  settings: config.settings,
  groupBy: config.groupBy,
  renderGroupHeader: config.renderGroupHeader,
  sortGroups: config.sortGroups,
  defaultGroupsCollapsed: config.defaultGroupsCollapsed,
  treeNodes: typeof config.transformers.getTreeNodes === "function" ? config.transformers.getTreeNodes(data) : undefined,
  defaultTreeCollapsed: config.defaultTreeCollapsed,
  popoverWidth: config.popoverWidth,
  isLoading,
  onOpen,
});

const getRangeFilterProps = ({ config, onChange, appliedFilters }) => ({
  label: config.label,
  onChange: onChange(config.id),
  appliedRange: config.transformers.getAppliedRange(appliedFilters[config.id]),
});

const ResourceFilters = ({ filters, appliedFilters, onAppliedFiltersChange, startDate, endDate, invoiceMonths = [], periodType }) => {
  const intl = useIntl();
  const { organizationId } = useOrganizationInfo();
  const hasKubernetesDataSource = useHasKubernetesDataSource();

  const handleChange = (type) => (selectedItems) => {
    if (type === "noTag" && !isEmptyArray(selectedItems.values)) {
      onAppliedFiltersChange({
        noTag: selectedItems,
        tag: FILTER_CONFIGS.tag.getDefaultValue(),
      });
      return;
    }

    if (type === "tag" && !isEmptyArray(selectedItems.values)) {
      onAppliedFiltersChange({
        tag: selectedItems,
        noTag: FILTER_CONFIGS.noTag.getDefaultValue(),
      });
      return;
    }

    onAppliedFiltersChange({
      [type]: selectedItems,
    });
  };

  const handleRangeChange = (type) => (selectedRange) => {
    onAppliedFiltersChange({
      [type]: {
        from: selectedRange.from ? moveDateFromUTC(startOfDay(selectedRange.from)) : undefined,
        to: selectedRange.to ? moveDateFromUTC(endOfDay(selectedRange.to)) : undefined,
      },
    });
  };

  const [showMoreFilters, setShowMoreFilters] = useState(false);
  const [tagFacetKeys, setTagFacetKeys] = useState<{ tag: string[]; without_tag: string[] } | null>(null);
  const [metaFacetKeys, setMetaFacetKeys] = useState<string[] | null>(null);

  const hasAppliedTag = FILTER_CONFIGS.tag.isApplied(appliedFilters.tag);
  const hasAppliedWithoutTag = FILTER_CONFIGS.withoutTag.isApplied(appliedFilters.withoutTag);
  const hasAppliedMeta = FILTER_CONFIGS.meta.isApplied(appliedFilters.meta);

  const needsTagFacets = showMoreFilters || hasAppliedTag || hasAppliedWithoutTag;
  const needsMetaFacets = showMoreFilters || hasAppliedMeta;

  const lazyFacets = useMemo(() => {
    const parts = [];
    if (needsTagFacets) {
      parts.push("tag");
    }
    if (needsMetaFacets) {
      parts.push("meta");
    }
    return parts.join(",");
  }, [needsTagFacets, needsMetaFacets]);

  const {
    data: lazyFacetsData,
    loading: isLazyFacetsLoading,
    refetch: refetchLazyFacets,
  } = useAvailableFiltersQuery({
    skip:
      !lazyFacets ||
      !organizationId ||
      (periodType === COST_PERIOD_BILLING && !invoiceMonths?.length) ||
      (invoiceMonths?.length ? false : startDate == null || endDate == null),
    notifyOnNetworkStatusChange: true,
    variables: {
      organizationId,
      params: invoiceMonths?.length
        ? {
            invoice_months: invoiceMonths,
            facets: lazyFacets,
          }
        : {
            start_date: startDate,
            end_date: endDate,
            facets: lazyFacets,
          },
    },
  });

  useEffect(() => {
    const lazy = lazyFacetsData?.availableFilters;
    if (!lazy) {
      return;
    }
    if (Array.isArray(lazy.tag) || Array.isArray(lazy.without_tag)) {
      setTagFacetKeys({
        tag: (lazy.tag as string[]) ?? [],
        without_tag: (lazy.without_tag as string[]) ?? [],
      });
    }
    if (Array.isArray(lazy.meta)) {
      setMetaFacetKeys(lazy.meta as string[]);
    }
  }, [lazyFacetsData]);

  // Reset cached lazy facets when the date range changes.
  useEffect(() => {
    setTagFacetKeys(null);
    setMetaFacetKeys(null);
  }, [startDate, endDate, invoiceMonths]);

  const isTagFacetsLoading = needsTagFacets && (isLazyFacetsLoading || tagFacetKeys === null);
  const isMetaFacetsLoading = needsMetaFacets && (isLazyFacetsLoading || metaFacetKeys === null);

  const requestTagFacetsReload = () => {
    if (!needsTagFacets || isLazyFacetsLoading) {
      return;
    }
    if (tagFacetKeys === null || isEmptyArray(tagFacetKeys.tag)) {
      void refetchLazyFacets();
    }
  };

  const requestMetaFacetsReload = () => {
    if (!needsMetaFacets || isLazyFacetsLoading) {
      return;
    }
    if (metaFacetKeys === null || isEmptyArray(metaFacetKeys)) {
      void refetchLazyFacets();
    }
  };

  const mergedFilters = useMemo(
    () => ({
      ...filters,
      tag: tagFacetKeys?.tag ?? filters.tag ?? [],
      without_tag: tagFacetKeys?.without_tag ?? filters.without_tag ?? [],
      meta: metaFacetKeys ?? filters.meta ?? [],
    }),
    [filters, tagFacetKeys, metaFacetKeys]
  );

  const toggleShowMoreFilters = () => {
    setShowMoreFilters((prev) => !prev);
  };

  const handleResetFilters = () => {
    onAppliedFiltersChange(
      Object.fromEntries(Object.values(FILTER_CONFIGS).map((config) => [config.id, config.getDefaultValue()]))
    );
  };

  const { id: currentEmployeeId } = useCurrentEmployee();

  const suggestionGroups = [
    {
      id: "ownerId",
      title: <FormattedMessage id="owner" />,
      items:
        mergedFilters.owner
          ?.filter((item) => item.id === currentEmployeeId)
          .map((item) => ({
            name: intl.formatMessage({ id: "assignedToMe" }),
            value: item.id,
          })) ?? [],
      renderItem: (item) => item.name,
    },
    {
      id: "resourceType",
      title: <FormattedMessage id="resourceType" />,
      items:
        mergedFilters.resource_type
          ?.filter((item) => ["Volume", "Instance"].includes(item.name))
          .map((item) => ({
            name: item.name,
            value: `${item.name}:${item.type}`,
          })) ?? [],
      renderItem: (item) => item.name,
    },
    {
      id: "active",
      title: <FormattedMessage id="activity" />,
      items:
        mergedFilters.active
          ?.filter((item) => item === true)
          .map((item) => ({
            name: intl.formatMessage({ id: "active" }),
            value: item,
          })) ?? [],
      renderItem: (item) => item.name,
    },
    {
      id: "constraintViolated",
      title: <FormattedMessage id="constraintViolations" />,
      items:
        mergedFilters.constraint_violated
          ?.filter((item) => item === true)
          .map((item) => ({
            name: intl.formatMessage({ id: "violated" }),
            value: item,
          })) ?? [],
      renderItem: (item) => item.name,
    },
  ];

  const handleApplySuggestion = (updates: Record<string, string[]>) => {
    onAppliedFiltersChange({
      ownerId: { values: updates.ownerId || [] },
      resourceType: { values: updates.resourceType || [] },
      active: { values: updates.active || [] },
      constraintViolated: { values: updates.constraintViolated || [] },
    });
  };

  const lazyFilterProps = {
    tag: { isLoading: isTagFacetsLoading, onOpen: requestTagFacetsReload },
    withoutTag: { isLoading: isTagFacetsLoading, onOpen: requestTagFacetsReload },
    meta: { isLoading: isMetaFacetsLoading, onOpen: requestMetaFacetsReload },
  };

  const FILTER_GROUPS = {
    primary: [
      { key: "cloudAccountId", data: mergedFilters.cloud_account },
      { key: "poolId", data: mergedFilters.pool },
      { key: "ownerId", data: mergedFilters.owner },
      { key: "region", data: mergedFilters.region },
      { key: "serviceName", data: mergedFilters.service_name },
      { key: "resourceType", data: mergedFilters.resource_type },
      { key: "active", data: mergedFilters.active },
      { key: "recommendations", data: mergedFilters.recommendations },
      { key: "constraintViolated", data: mergedFilters.constraint_violated },
    ],
    range: [{ key: "firstSeen" }, { key: "lastSeen" }],
    secondary: [
      { key: "tag", data: mergedFilters.tag },
      { key: "withoutTag", data: mergedFilters.without_tag },
      { key: "noTag", data: true },
      { key: "meta", data: mergedFilters.meta },
      { key: "virtualTag", data: mergedFilters.virtual_tag },
      { key: "accountLocator", data: mergedFilters.account_locator },
      { key: "networkTrafficFrom", data: mergedFilters.traffic_from },
      { key: "networkTrafficTo", data: mergedFilters.traffic_to },
      { key: "k8sNode", data: mergedFilters.k8s_node },
      { key: "k8sService", data: mergedFilters.k8s_service },
      { key: "k8sNamespace", data: mergedFilters.k8s_namespace },
    ],
  };

  const secondaryFilters = hasKubernetesDataSource
    ? FILTER_GROUPS.secondary
    : FILTER_GROUPS.secondary.filter(({ key }) => !(K8S_RESOURCE_FILTER_IDS as readonly string[]).includes(key));

  const hasAppliedValue = (key) => {
    const config = FILTER_CONFIGS[key];
    return config.isApplied(appliedFilters[key]);
  };

  const appliedSecondaryFilters = secondaryFilters.filter(({ key }) => hasAppliedValue(key));

  const renderSelectionFilter = ({ key, data }) => (
    <SelectionFilter
      key={key}
      {...getSelectionFilterProps({
        config: FILTER_CONFIGS[key],
        onChange: handleChange,
        appliedFilters,
        data,
        isLoading: lazyFilterProps[key]?.isLoading,
        onOpen: lazyFilterProps[key]?.onOpen,
      })}
    />
  );

  return (
    <Box display="flex" gap={2} flexWrap="wrap">
      <SuggestionFilter
        suggestionGroups={suggestionGroups}
        label={<FormattedMessage id="suggestions" />}
        onApplySuggestion={handleApplySuggestion}
        appliedFilters={{
          ownerId: appliedFilters.ownerId.values,
          resourceType: appliedFilters.resourceType.values,
          active: appliedFilters.active.values,
          constraintViolated: appliedFilters.constraintViolated.values,
        }}
      />
      {FILTER_GROUPS.primary.map(({ key, data }) => (
        <SelectionFilter
          key={key}
          {...getSelectionFilterProps({ config: FILTER_CONFIGS[key], onChange: handleChange, appliedFilters, data })}
        />
      ))}
      {FILTER_GROUPS.range.map(({ key }) => (
        <RangeFilter
          key={key}
          {...getRangeFilterProps({ config: FILTER_CONFIGS[key], onChange: handleRangeChange, appliedFilters })}
        />
      ))}
      {showMoreFilters
        ? secondaryFilters.map(renderSelectionFilter)
        : appliedSecondaryFilters.map(renderSelectionFilter)}
      {
        // Do not show the button if all secondary filters are applied
        appliedSecondaryFilters.length === secondaryFilters.length ? null : (
          <Badge
            badgeContent={showMoreFilters ? null : secondaryFilters.length - appliedSecondaryFilters.length}
            color="primary"
          >
            <Button
              variant="text"
              color="primary"
              endIcon={showMoreFilters ? <ExpandLessIcon /> : <ExpandMoreIcon />}
              onClick={toggleShowMoreFilters}
            >
              <FormattedMessage id={showMoreFilters ? "showLess" : "showMore"} />
            </Button>
          </Badge>
        )
      }
      {/* Reset filters button */}
      {Object.keys(appliedFilters).some((key) => hasAppliedValue(key)) && (
        <Button variant="text" color="primary" startIcon={<RestartAltOutlinedIcon />} onClick={handleResetFilters}>
          <FormattedMessage id="resetFilters" />
        </Button>
      )}
    </Box>
  );
};

export default ResourceFilters;
