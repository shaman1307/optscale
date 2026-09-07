import { useMemo } from "react";
import { KUBERNETES_CNR } from "utils/constants";
import { useAllDataSources } from "./coreData/useAllDataSources";

export const useHasKubernetesDataSource = () => {
  const dataSources = useAllDataSources();

  return useMemo(() => dataSources.some((dataSource) => dataSource.type === KUBERNETES_CNR), [dataSources]);
};

export default useHasKubernetesDataSource;
