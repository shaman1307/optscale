import { useDispatch } from "react-redux";
import { useRootData } from "hooks/useRootData";
import { COLUMNS, saveHiddenColumns } from "reducers/columns";

export const useColumnsVisibility = (columnsSelectorUID, defaultHiddenColumns = []) => {
  const dispatch = useDispatch();

  const { rootData: savedHiddenColumns } = useRootData(COLUMNS, (result) => result?.[columnsSelectorUID]);
  const hiddenColumns = Array.isArray(savedHiddenColumns)
    ? savedHiddenColumns
    : Array.isArray(defaultHiddenColumns)
      ? defaultHiddenColumns
      : [];

  const columnVisibility = Object.fromEntries(hiddenColumns.map((colName) => [colName, false]));

  const onColumnVisibilityChange = (stateUpdater) => {
    // stateUpdater is an object in case the getToggleAllColumnsVisibilityHandler() were applied to toggle the visibility state
    const newColumnsVisibleState = typeof stateUpdater === "function" ? stateUpdater(columnVisibility) : stateUpdater;

    const nextHiddenColumns = Object.entries(newColumnsVisibleState)
      .filter(([, isVisible]) => !isVisible)
      .map(([colName]) => colName);

    dispatch(saveHiddenColumns(columnsSelectorUID, nextHiddenColumns));
  };

  return {
    state: {
      columnVisibility,
    },
    tableOptions: {
      onColumnVisibilityChange,
    },
  };
};
