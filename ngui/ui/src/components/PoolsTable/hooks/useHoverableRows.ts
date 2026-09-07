import { useEffect } from "react";
import { useDispatch } from "react-redux";
import { useInitialMount } from "hooks/useInitialMount";
import { useRootData } from "hooks/useRootData";
import { useSyncQueryParamWithState } from "hooks/useSyncQueryParamWithState";
import { POOL_QUERY_PARAM_NAME } from "urls";
import { getRecursiveParent } from "utils/arrays";
import { getPoolTypeGroupId, isPoolTypeGroup } from "utils/pools";
import { setExpandedRows } from "../actionCreators";
import { EXPANDED_POOL_ROWS } from "../reducer";

const useHoverableRows = ({ onClick, rootPool, isGetPoolDataReady }) => {
  const dispatch = useDispatch();
  const [selectedPool, setSelectedPool] = useSyncQueryParamWithState({
    queryParamName: POOL_QUERY_PARAM_NAME,
    defaultValue: "",
  });

  const handleRowClick = (pool) => {
    if (isPoolTypeGroup(pool)) {
      const isExpanded = expandedPoolIds.includes(pool.id);
      dispatch(setExpandedRows(isExpanded ? expandedPoolIds.filter((id) => id !== pool.id) : [...expandedPoolIds, pool.id]));
      return;
    }
    setSelectedPool(pool.id);
    onClick(pool.id);
  };
  const isSelectedRow = ({ id }) => id === selectedPool;
  const { isInitialMount, setIsInitialMount } = useInitialMount();
  const { rootData: expandedPoolIds = [] } = useRootData(EXPANDED_POOL_ROWS);

  // opening rows to make selected pool visible on initial load
  useEffect(() => {
    if (!isGetPoolDataReady || !isInitialMount) {
      return;
    }
    const pools = [rootPool, ...(rootPool.children || [])];
    const selectedPoolInfo = pools.find(({ id }) => id === selectedPool);

    if (selectedPoolInfo) {
      const typeGroupId = selectedPoolInfo.parent_id
        ? getPoolTypeGroupId(selectedPoolInfo.parent_id, selectedPoolInfo.purpose || "budget")
        : null;
      const expandedMerged = [
        ...expandedPoolIds,
        ...getRecursiveParent(selectedPoolInfo, pools, "id"),
        ...(typeGroupId ? [typeGroupId] : []),
      ];
      const expandedUnique = [...new Set(expandedMerged)];
      dispatch(setExpandedRows(expandedUnique));
    }

    setIsInitialMount(false);
  }, [isGetPoolDataReady, dispatch, rootPool, selectedPool, isInitialMount, setIsInitialMount, expandedPoolIds]);

  return { isSelectedRow, handleRowClick };
};
export default useHoverableRows;
