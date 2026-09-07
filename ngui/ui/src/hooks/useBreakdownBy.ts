import { useEffect, useMemo } from "react";
import { intl } from "translations/react-intl-config";
import { RESOURCES_EXPENSES_DAILY_BREAKDOWN_BY, RESOURCES_EXPENSES_DAILY_BREAKDOWN_BY_VALUES } from "utils/constants";
import { updateSearchParams } from "utils/network";
import { getVirtualTagKeyFromFilterBy, isVirtualTagBreakdown } from "utils/virtualTagBreakdown";
import { useHasKubernetesDataSource } from "./useHasKubernetesDataSource";
import { useReactiveSearchParams } from "./useReactiveSearchParams";

const getBreakdownDefinition = (value: string, messageId: string, name?: string) => ({
  value,
  name: name ?? intl.formatMessage({ id: messageId }),
});

const serviceNameBreakdown = getBreakdownDefinition(RESOURCES_EXPENSES_DAILY_BREAKDOWN_BY.SERVICE_NAME, "service");

const regionBreakdown = getBreakdownDefinition(RESOURCES_EXPENSES_DAILY_BREAKDOWN_BY.REGION, "region");

const resourceTypeBreakdown = getBreakdownDefinition(RESOURCES_EXPENSES_DAILY_BREAKDOWN_BY.RESOURCE_TYPE, "resourceType");

const dataSourceBreakdown = getBreakdownDefinition(RESOURCES_EXPENSES_DAILY_BREAKDOWN_BY.CLOUD_ACCOUNT_ID, "dataSource");

const ownerBreakdown = getBreakdownDefinition(RESOURCES_EXPENSES_DAILY_BREAKDOWN_BY.EMPLOYEE_ID, "owner");

const poolBreakdown = getBreakdownDefinition(RESOURCES_EXPENSES_DAILY_BREAKDOWN_BY.POOL_ID, "pool");

const subpoolBreakdown = getBreakdownDefinition(RESOURCES_EXPENSES_DAILY_BREAKDOWN_BY.SUBPOOL, "subpool");

const k8sNodeBreakdown = getBreakdownDefinition(RESOURCES_EXPENSES_DAILY_BREAKDOWN_BY.K8S_NODE, "k8sNode");

const k8sNamespaceBreakdown = getBreakdownDefinition(RESOURCES_EXPENSES_DAILY_BREAKDOWN_BY.K8S_NAMESPACE, "k8sNamespace");

const k8sServiceBreakdown = getBreakdownDefinition(RESOURCES_EXPENSES_DAILY_BREAKDOWN_BY.K8S_SERVICE, "k8sService");

const accountLocatorBreakdown = getBreakdownDefinition(RESOURCES_EXPENSES_DAILY_BREAKDOWN_BY.ACCOUNT_LOCATOR, "sfAccount");

export const breakdowns = Object.freeze([
  serviceNameBreakdown,
  regionBreakdown,
  resourceTypeBreakdown,
  dataSourceBreakdown,
  ownerBreakdown,
  poolBreakdown,
  subpoolBreakdown,
  accountLocatorBreakdown,
  k8sNodeBreakdown,
  k8sNamespaceBreakdown,
  k8sServiceBreakdown,
]);

const K8S_BREAKDOWN_VALUES = new Set([
  RESOURCES_EXPENSES_DAILY_BREAKDOWN_BY.K8S_NODE,
  RESOURCES_EXPENSES_DAILY_BREAKDOWN_BY.K8S_NAMESPACE,
  RESOURCES_EXPENSES_DAILY_BREAKDOWN_BY.K8S_SERVICE,
]);

export const getVisibleBreakdowns = (hasKubernetes: boolean) =>
  hasKubernetes ? breakdowns : breakdowns.filter((breakdown) => !K8S_BREAKDOWN_VALUES.has(breakdown.value));

export const getBreakdownDisplayName = (value?: string) => {
  const found = breakdowns.find((breakdown) => breakdown.value === value);
  if (found) {
    return found.name;
  }
  if (isVirtualTagBreakdown(value)) {
    return getVirtualTagKeyFromFilterBy(value);
  }
  return value;
};

export const useBreakdownBy = ({
  queryParamName,
  extraBreakdowns = [],
}: {
  queryParamName: string;
  extraBreakdowns?: { value: string; name: string }[];
}) => {
  const hasKubernetes = useHasKubernetesDataSource();
  const searchParams = useReactiveSearchParams(useMemo(() => [queryParamName], [queryParamName]));

  const breakdownByQueryParameterValue = searchParams[queryParamName];

  const visibleBreakdowns = useMemo(() => getVisibleBreakdowns(hasKubernetes), [hasKubernetes]);
  const allBreakdowns = useMemo(() => [...visibleBreakdowns, ...extraBreakdowns], [visibleBreakdowns, extraBreakdowns]);

  const breakdownBy = RESOURCES_EXPENSES_DAILY_BREAKDOWN_BY_VALUES.includes(breakdownByQueryParameterValue)
    ? allBreakdowns.find(({ value }) => value === breakdownByQueryParameterValue) ?? serviceNameBreakdown
    : isVirtualTagBreakdown(breakdownByQueryParameterValue)
      ? allBreakdowns.find(({ value }) => value === breakdownByQueryParameterValue) || {
          value: breakdownByQueryParameterValue,
          name: getVirtualTagKeyFromFilterBy(breakdownByQueryParameterValue),
        }
      : serviceNameBreakdown;

  useEffect(() => {
    if (!searchParams[queryParamName] || (K8S_BREAKDOWN_VALUES.has(searchParams[queryParamName]) && !hasKubernetes)) {
      updateSearchParams({
        [queryParamName]: serviceNameBreakdown.value,
      });
    }
  }, [hasKubernetes, queryParamName, searchParams]);

  const onBreakdownByChange = (newBreakdownByValue: string) => {
    updateSearchParams({
      [queryParamName]: newBreakdownByValue,
    });
  };

  return [breakdownBy, onBreakdownByChange];
};
