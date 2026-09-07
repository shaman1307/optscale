import { useMemo, useState } from "react";
import { getPaginationRowModel } from "@tanstack/react-table";
import { getSearchParams, updateSearchParams } from "utils/network";
import { getPaginationQueryKey } from "utils/tables";

const getPageQueryParamValue = (key) => {
  const { [key]: page = 1 } = getSearchParams();

  const pageNumber = Number(page);

  if (Number.isNaN(pageNumber) || !Number.isInteger(pageNumber) || pageNumber <= 0) {
    return 1;
  }

  return pageNumber;
};

const getPageIndexQueryParamValue = (key) => {
  const page = getPageQueryParamValue(key);
  return page - 1;
};

const handleChange = (state, changeState) => (updater) => {
  changeState(updater(state));
};

export const usePaginationTableSettings = ({ pageSize, rowsCount, queryParamPrefix, enablePaginationQueryParam = true }) => {
  const queryKeyForPage = getPaginationQueryKey(queryParamPrefix);

  const isPaginationEnabled = Number.isInteger(pageSize) && pageSize > 0;

  const [pagination, setPagination] = useState(() => ({
    pageSize,
    pageIndex: enablePaginationQueryParam ? getPageIndexQueryParamValue(queryKeyForPage) : 0,
  }));

  const paginationState = useMemo(() => {
    const pagesCount = Math.ceil(rowsCount / pageSize);
    const lastPageIndex = pagesCount - 1;
    const pageIndex = Math.min(lastPageIndex, pagination.pageIndex);

    return {
      pageSize: pagination.pageSize,
      pageIndex,
    };
  }, [pageSize, pagination.pageIndex, pagination.pageSize, rowsCount]);

  // Always register getPaginationRowModel. With paginateExpandedRows: false,
  // TanStack only flattens expanded subRows inside this model — skipping it
  // (e.g. PoolsTable without pageSize) leaves chevrons working and totals
  // updating via getSubRows while child rows never render.
  const paginationRowModelOption = {
    getPaginationRowModel: getPaginationRowModel(),
  };

  if (isPaginationEnabled) {
    return {
      state: {
        pagination: paginationState,
      },
      tableOptions: {
        autoResetPageIndex: false,
        ...paginationRowModelOption,
        onPaginationChange: handleChange(pagination, (newPaginationState) => {
          if (enablePaginationQueryParam) {
            updateSearchParams({ [queryKeyForPage]: newPaginationState.pageIndex + 1 });
          }
          setPagination(newPaginationState);
        }),
      },
    };
  }

  return {
    state: {
      pagination: {
        pageSize: rowsCount,
        pageIndex: 0,
      },
    },
    tableOptions: paginationRowModelOption,
  };
};
