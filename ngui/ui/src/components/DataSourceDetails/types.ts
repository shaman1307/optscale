import { FC } from "react";
import {
  ALIBABA_CNR,
  AWS_CNR,
  AZURE_CNR,
  AZURE_TENANT,
  GCP_CNR,
  KUBERNETES_CNR,
  NEBIUS,
  DATABRICKS,
  SNOWFLAKE,
  GCP_TENANT,
} from "utils/constants";
import {
  AlibabaPropertiesProps,
  AwsPropertiesProps,
  AzurePropertiesProps,
  DatabricksPropertiesProps,
  SnowflakePropertiesProps,
  GcpPropertiesProps,
  K8sPropertiesProps,
  NebiusPropertiesProps,
} from "./Properties/types";

type CloudAccountType =
  | typeof AWS_CNR
  | typeof AZURE_CNR
  | typeof AZURE_TENANT
  | typeof GCP_CNR
  | typeof GCP_TENANT
  | typeof ALIBABA_CNR
  | typeof KUBERNETES_CNR
  | typeof NEBIUS
  | typeof DATABRICKS
  | typeof SNOWFLAKE;

export type ConfigMap =
  | AlibabaPropertiesProps["config"]
  | AwsPropertiesProps["config"]
  | AzurePropertiesProps["config"]
  | DatabricksPropertiesProps["config"]
  | SnowflakePropertiesProps["config"]
  | GcpPropertiesProps["config"]
  | K8sPropertiesProps["config"]
  | NebiusPropertiesProps["config"];

export type PropertiesMap = {
  [AWS_CNR]: FC<AwsPropertiesProps>;
  [AZURE_CNR]: FC<AzurePropertiesProps>;
  [AZURE_TENANT]: FC<AzurePropertiesProps>;
  [GCP_CNR]: FC<GcpPropertiesProps>;
  [GCP_TENANT]: FC<GcpPropertiesProps>;
  [ALIBABA_CNR]: FC<AlibabaPropertiesProps>;
  [KUBERNETES_CNR]: FC<K8sPropertiesProps>;
  [NEBIUS]: FC<NebiusPropertiesProps>;
  [DATABRICKS]: FC<DatabricksPropertiesProps>;
  [SNOWFLAKE]: FC<SnowflakePropertiesProps>;
};

export type DataSourceDetailsProps = {
  id: string;
  accountId: string;
  parentId: string;
  createdAt: number;
  type: CloudAccountType;
  config: ConfigMap;
};
