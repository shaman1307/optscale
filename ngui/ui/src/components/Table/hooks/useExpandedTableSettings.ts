import { useState } from "react";
import { getExpandedRowModel } from "@tanstack/react-table";
import { isObject } from "utils/objects";
import { handleChange } from "../utils";

export const useExpandedTableSettings = ({ withExpanded, getSubRows, getRowCanExpand, expanded, onExpandedChange }) => {
  const isControlled = withExpanded && !!expanded && isObject(expanded) && typeof onExpandedChange === "function";
  const [uncontrolledExpanded, setUncontrolledExpanded] = useState({});

  if (!withExpanded) {
    return {
      state: {},
      tableOptions: {},
      expanded: {},
    };
  }

  const expandedState = isControlled ? expanded : uncontrolledExpanded;

  return {
    state: {
      expanded: expandedState,
    },
    tableOptions: {
      getExpandedRowModel: getExpandedRowModel(),
      getSubRows,
      ...(typeof getRowCanExpand === "function" ? { getRowCanExpand } : {}),
      // Keep children under their parent page. Child lists are paginated by the
      // caller (e.g. CloudAccountsTable) so sibling roots stay visible.
      // Requires getPaginationRowModel (see usePaginationTableSettings): with
      // this flag TanStack flattens expanded subRows only there, not in
      // getExpandedRowModel.
      paginateExpandedRows: false,
      onExpandedChange: isControlled
        ? handleChange(expanded, onExpandedChange)
        : handleChange(uncontrolledExpanded, setUncontrolledExpanded),
    },
    expanded: expandedState,
  };
};
