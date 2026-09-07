import {
  ALIBABA_CNR,
  AWS_CNR,
  AZURE_CNR,
  AZURE_TENANT,
  DATABRICKS,
  ENVIRONMENT,
  GCP_CNR,
  GCP_TENANT,
  KUBERNETES_CNR,
  NEBIUS,
  SNOWFLAKE,
  SNOWFLAKE_TENANT,
} from "utils/constants";

export type DataSourceVendor = {
  id: string;
  /** intl message id for short vendor label (AWS, GCP, …) */
  labelMessageId: string;
  /** cloud account type used for vendor icon (one representative type) */
  iconType: string;
};

const VENDOR_BY_TYPE: Record<string, DataSourceVendor> = {
  [AWS_CNR]: { id: "aws", labelMessageId: "aws", iconType: AWS_CNR },
  [GCP_CNR]: { id: "gcp", labelMessageId: "gcp", iconType: GCP_CNR },
  [GCP_TENANT]: { id: "gcp", labelMessageId: "gcp", iconType: GCP_CNR },
  [AZURE_CNR]: { id: "azure", labelMessageId: "azure", iconType: AZURE_CNR },
  [AZURE_TENANT]: { id: "azure", labelMessageId: "azure", iconType: AZURE_CNR },
  [SNOWFLAKE]: { id: "snowflake", labelMessageId: "snowflake", iconType: SNOWFLAKE },
  [SNOWFLAKE_TENANT]: { id: "snowflake", labelMessageId: "snowflake", iconType: SNOWFLAKE },
  [ALIBABA_CNR]: { id: "alibaba", labelMessageId: "alibaba", iconType: ALIBABA_CNR },
  [DATABRICKS]: { id: "databricks", labelMessageId: "databricks", iconType: DATABRICKS },
  [NEBIUS]: { id: "nebius", labelMessageId: "nebius", iconType: NEBIUS },
  [KUBERNETES_CNR]: { id: "kubernetes", labelMessageId: "kubernetes", iconType: KUBERNETES_CNR },
  [ENVIRONMENT]: { id: "environment", labelMessageId: "environment", iconType: ENVIRONMENT },
};

/** Stable display order for Data Source vendor groups. */
export const DATA_SOURCE_VENDOR_ORDER = [
  "aws",
  "azure",
  "gcp",
  "snowflake",
  "alibaba",
  "databricks",
  "nebius",
  "kubernetes",
  "environment",
] as const;

export const getDataSourceVendor = (type?: string | null): DataSourceVendor => {
  if (type && VENDOR_BY_TYPE[type]) {
    return VENDOR_BY_TYPE[type];
  }
  return {
    id: "unknown",
    labelMessageId: "unknown",
    iconType: type || ENVIRONMENT,
  };
};

export const getDataSourceVendorById = (vendorId: string): DataSourceVendor => {
  const vendor = Object.values(VENDOR_BY_TYPE).find(({ id }) => id === vendorId);
  if (vendor) {
    return vendor;
  }
  return {
    id: vendorId,
    labelMessageId: vendorId,
    iconType: vendorId,
  };
};

export const compareDataSourceVendors = (a: string, b: string): number => {
  const ai = DATA_SOURCE_VENDOR_ORDER.indexOf(a as (typeof DATA_SOURCE_VENDOR_ORDER)[number]);
  const bi = DATA_SOURCE_VENDOR_ORDER.indexOf(b as (typeof DATA_SOURCE_VENDOR_ORDER)[number]);
  const aOrder = ai === -1 ? DATA_SOURCE_VENDOR_ORDER.length : ai;
  const bOrder = bi === -1 ? DATA_SOURCE_VENDOR_ORDER.length : bi;
  if (aOrder !== bOrder) {
    return aOrder - bOrder;
  }
  return a.localeCompare(b);
};
