/**
 * Count rows that TanStack shows when paginateExpandedRows is true:
 * every root plus children of currently expanded parents only.
 * Do not count collapsed subtrees (that inflated pageCount when collapsed).
 */
export const countVisibleFlatRows = (
  data: unknown[],
  expanded: true | Record<string, boolean> | undefined,
  getSubRows: (row: any) => any[] | undefined = (row) => row?.children,
  getRowId?: (originalRow: any, index: number, parent?: { id: string }) => string
) => {
  if (!Array.isArray(data) || data.length === 0) {
    return 0;
  }

  if (expanded === true) {
    const countAll = (rows: unknown[]): number =>
      rows.reduce((acc, row) => {
        const subRows = getSubRows(row) || [];
        return acc + 1 + (subRows.length ? countAll(subRows) : 0);
      }, 0);
    return countAll(data);
  }

  const expandedMap = expanded && typeof expanded === "object" ? expanded : {};

  const walk = (rows: unknown[], parent?: { id: string }): number => {
    let count = 0;
    rows.forEach((row, index) => {
      const id = getRowId ? getRowId(row, index, parent) : parent ? `${parent.id}.${index}` : `${index}`;
      count += 1;
      const subRows = getSubRows(row) || [];
      if (subRows.length > 0 && expandedMap[id]) {
        count += walk(subRows, { id });
      }
    });
    return count;
  };

  return walk(data);
};
