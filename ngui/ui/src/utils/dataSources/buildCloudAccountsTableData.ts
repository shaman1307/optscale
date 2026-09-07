import { AWS_CNR } from "utils/constants";
import { hasDataSourceHealthIssue } from "./dataSourceHealth";
import { summarizeChildrenDetails } from "./summarizeTenantChildren";

export const AWS_SYNTHETIC_TENANT_ID = "__aws_tenant__";

export const isAwsSyntheticTenantId = (id?: string | null) => id === AWS_SYNTHETIC_TENANT_ID;

/** Prefer management/standalone (CUR) account; fall back to first AWS account. */
export const getAwsBillingRootAccount = (awsAccounts = []) =>
  awsAccounts.find((account) => !account?.config?.linked) ?? awsAccounts[0];

/**
 * UI-only AWS tenant payload for the details page (Snowflake-tenant-like).
 * Costs roll up from all AWS accounts; Advanced/import fields come from the billing root.
 */
export const buildSyntheticAwsTenantDetails = (dataSources = [], tenantName) => {
  const awsAccounts = dataSources.filter(({ type }) => type === AWS_CNR);
  if (!awsAccounts.length) {
    return null;
  }

  const billingRoot = getAwsBillingRootAccount(awsAccounts);
  const childrenDetails = summarizeChildrenDetails(awsAccounts);
  const createdAts = awsAccounts.map(({ created_at: createdAt }) => createdAt).filter((value) => typeof value === "number");

  return {
    id: AWS_SYNTHETIC_TENANT_ID,
    name: tenantName,
    type: AWS_CNR,
    isSynthetic: true,
    parent_id: null,
    account_id: billingRoot?.account_id,
    created_at: createdAts.length ? Math.min(...createdAts) : billingRoot?.created_at,
    last_import_at: billingRoot?.last_import_at,
    last_import_attempt_at: billingRoot?.last_import_attempt_at,
    last_import_attempt_error: billingRoot?.last_import_attempt_error,
    last_getting_metrics_at: billingRoot?.last_getting_metrics_at,
    last_getting_metric_attempt_at: billingRoot?.last_getting_metric_attempt_at,
    last_getting_metric_attempt_error: billingRoot?.last_getting_metric_attempt_error,
    config: billingRoot?.config ?? {},
    details: {
      ...childrenDetails,
      discovery_infos: billingRoot?.details?.discovery_infos ?? billingRoot?.discovery_infos,
    },
    billingImportDataSourceId: billingRoot?.id,
  };
};

const getDiscoveryInfos = (dataSource) => {
  const infos = dataSource?.discovery_infos ?? dataSource?.details?.discovery_infos;
  if (Array.isArray(infos)) {
    return infos;
  }
  if (infos && typeof infos === "object") {
    return Object.values(infos);
  }
  return [];
};

export const getChildrenDiscoveryError = (children = []) => {
  for (const child of children) {
    for (const info of getDiscoveryInfos(child)) {
      const lastDiscoveryAt = info.last_discovery_at || 0;
      const lastErrorAt = info.last_error_at || 0;
      if (lastErrorAt === 0 && lastDiscoveryAt === 0) {
        continue;
      }
      if (lastErrorAt >= lastDiscoveryAt && info.last_error) {
        return `${child.name}: ${info.last_error}`;
      }
    }
  }
  return undefined;
};

const paginateChildren = (allChildren, parentId, childPageById, childPageSize) => {
  const childPageCount = Math.max(1, Math.ceil(allChildren.length / childPageSize));
  const childPageIndex = Math.min(childPageById[parentId] ?? 0, childPageCount - 1);
  const start = childPageIndex * childPageSize;
  return {
    children: allChildren.slice(start, start + childPageSize).map((child) => ({
      ...child,
      hasHealthIssue: hasDataSourceHealthIssue(child),
    })),
    allChildrenCount: allChildren.length,
    childPageIndex,
    childPageCount,
  };
};

const decorateParent = (dataSource, allAccounts, childPageById, childPageSize) => {
  const allChildren = allAccounts.filter(({ parent_id: parentId }) => parentId === dataSource.id);
  const childrenDetails = summarizeChildrenDetails(allChildren);
  const parentHealthIssue = hasDataSourceHealthIssue(dataSource) || allChildren.some(hasDataSourceHealthIssue);
  return {
    ...dataSource,
    ...paginateChildren(allChildren, dataSource.id, childPageById, childPageSize),
    childrenDiscoveryError: getChildrenDiscoveryError(allChildren),
    hasHealthIssue: parentHealthIssue,
    details: { ...dataSource.details, ...childrenDetails },
  };
};

const buildSyntheticAwsTenant = (awsAccounts, childPageById, childPageSize, tenantName) => {
  const sortedAws = [...awsAccounts].sort((left, right) =>
    String(left.name || "").localeCompare(String(right.name || ""))
  );
  const childrenDetails = summarizeChildrenDetails(sortedAws);
  return {
    id: AWS_SYNTHETIC_TENANT_ID,
    name: tenantName,
    type: AWS_CNR,
    isSynthetic: true,
    parent_id: null,
    ...paginateChildren(sortedAws, AWS_SYNTHETIC_TENANT_ID, childPageById, childPageSize),
    childrenDiscoveryError: getChildrenDiscoveryError(sortedAws),
    hasHealthIssue: sortedAws.some(hasDataSourceHealthIssue),
    details: childrenDetails,
  };
};

/**
 * Data Sources table rows: real parent_id tenants stay as-is.
 * All AWS accounts are nested under one synthetic AWS tenant (UI-only, no API id).
 */
export const buildCloudAccountsTableData = ({
  cloudAccounts = [],
  childPageById = {},
  childPageSize,
  awsTenantName,
}) => {
  const awsAccounts = cloudAccounts.filter(({ type }) => type === AWS_CNR);
  const otherAccounts = cloudAccounts.filter(({ type }) => type !== AWS_CNR);

  const otherRoots = otherAccounts
    .map((dataSource) => decorateParent(dataSource, otherAccounts, childPageById, childPageSize))
    .filter(({ parent_id: parentId }) => !parentId);

  if (!awsAccounts.length) {
    return otherRoots;
  }

  return [
    buildSyntheticAwsTenant(awsAccounts, childPageById, childPageSize, awsTenantName),
    ...otherRoots,
  ];
};
