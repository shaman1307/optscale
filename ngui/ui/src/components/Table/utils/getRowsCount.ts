const getCount = (tableData, getSubRows, depth = 0) =>
  tableData.reduce((acc, item) => {
    // Only walk one level for totals: deep recursion over large pool trees
    // (Profitero 400+ pools) freezes the Pools tab on every render.
    if (depth >= 1) {
      return acc + 1;
    }
    const subRows = getSubRows(item);
    return acc + 1 + (subRows?.length ? getCount(subRows, getSubRows, depth + 1) : 0);
  }, 0);

export const getRowsCount = (tableData, { withExpanded, getSubRows }) => {
  if (withExpanded) {
    return getCount(tableData, getSubRows);
  }

  return tableData.length;
};
