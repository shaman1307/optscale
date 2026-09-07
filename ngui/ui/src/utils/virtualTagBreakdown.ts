export const VIRTUAL_TAG_BREAKDOWN_PREFIX = "virtual_tag:";

export const isVirtualTagBreakdown = (value?: string | null): value is string =>
  typeof value === "string" && value.startsWith(VIRTUAL_TAG_BREAKDOWN_PREFIX);

export const getVirtualTagKeyFromFilterBy = (filterBy?: string | null) =>
  isVirtualTagBreakdown(filterBy) ? filterBy.slice(VIRTUAL_TAG_BREAKDOWN_PREFIX.length) : undefined;

const NOT_SET_ID = "(not set)";

const toSeriesItem = (key: string, info: Record<string, unknown> = {}) => {
  const id = (info.id as string | null | undefined) ?? (key === "null" || key === "undefined" ? NOT_SET_ID : key);
  const name = (info.name as string | undefined) ?? (id === NOT_SET_ID ? NOT_SET_ID : String(id));
  return { ...info, id, name };
};

/**
 * Convert organizations/{id}/breakdown_expenses payload into the
 * pools_expenses shape that ExpensesBreakdown already renders.
 */
export const mapBreakdownExpensesToFormatted = (apiData: Record<string, unknown> = {}) => {
  const breakdown = (apiData.breakdown || {}) as Record<string, Record<string, Record<string, unknown>>>;
  const counts = (apiData.counts || {}) as Record<string, Record<string, unknown>>;

  const formattedBreakdown = Object.fromEntries(
    Object.entries(breakdown).map(([timestamp, dayMap]) => [
      timestamp,
      Object.entries(dayMap || {}).map(([key, info]) => {
        const series = toSeriesItem(key, info);
        return {
          ...series,
          expense: Number(info?.cost ?? 0),
        };
      }),
    ])
  );

  const filteredBreakdown = Object.entries(counts).map(([key, info]) => {
    const series = toSeriesItem(key, info);
    return {
      ...series,
      total: Number(info?.total ?? 0),
      previous_total: Number(info?.previous_total ?? 0),
    };
  });

  return {
    breakdown: formattedBreakdown,
    filteredBreakdown,
    total: Number(apiData.total ?? 0),
    previousTotal: Number(apiData.previous_total ?? 0),
  };
};
