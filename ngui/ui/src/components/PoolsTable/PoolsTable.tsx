import { useCallback, useEffect, useMemo, useState } from "react";
import { FormattedMessage } from "react-intl";
import { useDispatch } from "react-redux";
import DashedTypography from "components/DashedTypography";
import { PoolModal } from "components/SideModalManager/SideModals";
import { POOL_TABS } from "components/SideModalManager/SideModals/PoolModal";
import Table from "components/Table";
import TableLoader from "components/TableLoader";
import Tooltip from "components/Tooltip";
import { useInScopeOfPageMockup } from "hooks/useInScopeOfPageMockup";
import { useOpenSideModal } from "hooks/useOpenSideModal";
import { useRootData } from "hooks/useRootData";
import { EDIT_POOL_TAB_QUERY } from "urls";
import { text } from "utils/columns";
import { updateSearchParams } from "utils/network";
import { buildPoolTypeGroupRows, isPoolTypeGroup } from "utils/pools";
import { setExpandedRows } from "./actionCreators";
import { expenses, poolActions, poolForecast, poolLimit, poolName } from "./columns";
import useExpandRequiresAttention from "./hooks/useExpandRequiresAttention";
import useGetRowStyle from "./hooks/useGetRowStyle";
import useHoverableRows from "./hooks/useHoverableRows";
import useStyles from "./PoolsTable.styles";
import { EXPANDED_POOL_ROWS } from "./reducer";

const CHILD_PAGE_SIZE = 50;

const PoolsTable = ({ rootPool, isLoadingProps = {} }) => {
  const dispatch = useDispatch();
  const { classes } = useStyles();

  const openSideModal = useOpenSideModal();
  const { isGetPoolLoading = false, isGetPoolDataReady } = isLoadingProps;

  const { id: rootPoolId, children: rootPoolChildren = [] } = rootPool;

  const openEditModal = useCallback(
    (tab, poolId) => {
      const info = [rootPool, ...rootPoolChildren].find(({ id }) => id === poolId);
      updateSearchParams({ [EDIT_POOL_TAB_QUERY]: tab });
      openSideModal(PoolModal, { id: poolId, info });
    },
    [openSideModal, rootPool, rootPoolChildren]
  );

  const { rootData: expandedPoolIds = [] } = useRootData(EXPANDED_POOL_ROWS);

  // Per pool-type-group child page index; independent from root expand state.
  const [childPageById, setChildPageById] = useState({});

  const onChildPageChange = useCallback((groupId, pageIndex) => {
    setChildPageById((prev) => ({ ...prev, [groupId]: pageIndex }));
  }, []);

  const expandRequiresAttentionHandler = useExpandRequiresAttention(rootPool);
  const isMocked = useInScopeOfPageMockup();
  useEffect(() => {
    if (isMocked) {
      expandRequiresAttentionHandler();
    }
  }, [isMocked, expandRequiresAttentionHandler]);
  const expanded = Object.fromEntries(expandedPoolIds.map((poolId) => [poolId, true]));
  const onExpandedChange = (newState) => {
    // TanStack may keep keys with false; only persist expanded ids.
    const newExpandedPoolIds =
      newState === true
        ? [rootPoolId]
        : Object.entries(newState || {})
            .filter(([, isExpanded]) => Boolean(isExpanded))
            .map(([poolId]) => poolId);

    dispatch(setExpandedRows(newExpandedPoolIds));
  };

  const hasChildPools = useCallback(
    (poolId) => rootPoolChildren.some((child) => child.parent_id === poolId),
    [rootPoolChildren]
  );

  const getRowCanExpand = useCallback(
    (row) => {
      const original = row.original;
      if (isPoolTypeGroup(original)) {
        return (original.childrenCount || 0) > 0;
      }
      return hasChildPools(original.id);
    },
    [hasChildPools]
  );

  const actionBarDefinition = {
    /*
      Action bar doesn't support mobile view for "custom" items
      See OSN-140
    */
    hideItemsOnSmallScreens: false,
    items: [
      {
        key: "expandRequiringAttention",
        type: "custom",
        node: (
          <Tooltip title={<FormattedMessage id="expandRequiresAttentionHelp" />}>
            <span>
              <DashedTypography onClick={expandRequiresAttentionHandler} dataTestId="expandRequiringAttention">
                <FormattedMessage id="expandRequiringAttention" />
              </DashedTypography>
            </span>
          </Tooltip>
        ),
      },
    ],
    poolId: rootPoolId,
  };

  const columns = useMemo(
    () => [
      poolName({
        onExpensesExportClick: (id) => openEditModal(POOL_TABS.SHARE, id),
        onConstraintsClick: (id) => openEditModal(POOL_TABS.CONSTRAINTS, id),
        onChildPageChange,
        classes,
      }),
      poolLimit(),
      expenses({ defaultSort: "desc" }),
      poolForecast(),
      text({
        headerMessageId: "owner",
        headerDataTestId: "lbl_owner",
        accessorKey: "default_owner_name",
        options: {
          columnSelector: {
            accessor: "default_owner_name",
            messageId: "owner",
            dataTestId: "btn_toggle_default_owner_name",
          },
        },
      }),
      poolActions(),
    ],
    [openEditModal, onChildPageChange, classes]
  );

  const root = useMemo(
    // Bust TanStack core-row memo when expand/page changes so lazy getSubRows re-runs.
    () => [
      {
        ...rootPool,
        hasNestedChildren: rootPoolChildren.some((child) => child.parent_id === rootPool.id),
        _poolTreeKey: `${expandedPoolIds.join("|")}:${JSON.stringify(childPageById)}`,
      },
    ],
    [rootPool, rootPoolChildren, expandedPoolIds, childPageById]
  );

  const getSubRows = useCallback(
    (parentObject) => {
      if (isPoolTypeGroup(parentObject)) {
        if (!expanded[parentObject.id]) {
          return [];
        }
        const allChildren = rootPoolChildren
          .filter((child) => child.parent_id === parentObject.parent_id && (child.purpose || "budget") === parentObject.purpose)
          .slice()
          .sort((left, right) => String(left.name || "").localeCompare(String(right.name || "")));
        const childPageCount = Math.max(1, Math.ceil(allChildren.length / CHILD_PAGE_SIZE));
        const childPageIndex = Math.min(childPageById[parentObject.id] ?? 0, childPageCount - 1);
        const start = childPageIndex * CHILD_PAGE_SIZE;
        return allChildren.slice(start, start + CHILD_PAGE_SIZE).map((child) => ({
          ...child,
          hasNestedChildren: rootPoolChildren.some((nested) => nested.parent_id === child.id),
        }));
      }

      if (!expanded[parentObject.id]) {
        return [];
      }

      const directChildren = rootPoolChildren.filter((child) => child.parent_id === parentObject.id);
      return buildPoolTypeGroupRows(parentObject.id, directChildren).map((group) => {
        const childPageCount = Math.max(1, Math.ceil((group.childrenCount || 0) / CHILD_PAGE_SIZE));
        const childPageIndex = Math.min(childPageById[group.id] ?? 0, childPageCount - 1);
        return {
          ...group,
          childPageCount,
          childPageIndex,
        };
      });
    },
    [rootPoolChildren, childPageById, expanded]
  );

  const { isSelectedRow, handleRowClick } = useHoverableRows({
    onClick: (selectedPoolId) => openEditModal(POOL_TABS.GENERAL, selectedPoolId),
    rootPool,
    isGetPoolDataReady,
  });

  const getRowStyle = useGetRowStyle(rootPool);

  return (
    <>
      {isGetPoolLoading ? (
        <TableLoader columnsCounter={columns.length} showHeader />
      ) : (
        <Table
          data={root}
          columns={columns}
          actionBar={{
            show: true,
            definition: actionBarDefinition,
          }}
          withExpanded
          getSubRows={getSubRows}
          getRowCanExpand={getRowCanExpand}
          getRowId={(row) => row.id}
          expanded={expanded}
          onExpandedChange={onExpandedChange}
          localization={{
            emptyMessageId: "noPools",
          }}
          columnsSelectorUID="poolsTable"
          getRowStyle={getRowStyle}
          onRowClick={handleRowClick}
          isSelectedRow={isSelectedRow}
        />
      )}
    </>
  );
};

export default PoolsTable;
