import { GCP_CNR, GCP_TENANT } from "utils/constants";
import { useAllDataSources } from "./coreData/useAllDataSources";

export const useHasGcpDataSource = () => {
  const dataSources = useAllDataSources();
  return dataSources.some((dataSource) => dataSource.type === GCP_CNR || dataSource.type === GCP_TENANT);
};
