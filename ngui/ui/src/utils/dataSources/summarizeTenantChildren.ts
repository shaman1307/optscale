import { isEmptyArray } from "utils/arrays";

const INITIAL_SUMS = {
  resources: 0,
  cost: 0,
  forecast: 0,
  last_month_cost: 0,
  total_cost: 0,
  total_resources: 0,
};

/**
 * Roll up child data-source details onto a tenant/root row.
 * Costs/resources are summed; billing data period is the union of children
 * (earliest from, latest to).
 */
export const summarizeChildrenDetails = (children) => {
  if (isEmptyArray(children)) {
    return {};
  }

  const sums = children.reduce(
    (
      acc,
      {
        details: {
          resources = 0,
          cost = 0,
          forecast = 0,
          last_month_cost: lastMonthCost = 0,
          total_cost: totalCost = 0,
          total_resources: totalResources = 0,
          billing_period_start: billingPeriodStart,
          billing_period_end: billingPeriodEnd,
        } = {},
      }
    ) => {
      const next = {
        resources: acc.resources + resources,
        cost: acc.cost + cost,
        forecast: acc.forecast + forecast,
        last_month_cost: acc.last_month_cost + lastMonthCost,
        total_cost: acc.total_cost + totalCost,
        total_resources: acc.total_resources + totalResources,
        billing_period_start: acc.billing_period_start,
        billing_period_end: acc.billing_period_end,
      };

      if (billingPeriodStart) {
        next.billing_period_start =
          acc.billing_period_start == null ? billingPeriodStart : Math.min(acc.billing_period_start, billingPeriodStart);
      }
      if (billingPeriodEnd) {
        next.billing_period_end =
          acc.billing_period_end == null ? billingPeriodEnd : Math.max(acc.billing_period_end, billingPeriodEnd);
      }

      return next;
    },
    {
      ...INITIAL_SUMS,
      billing_period_start: null,
      billing_period_end: null,
    }
  );

  const { billing_period_start: start, billing_period_end: end, ...totals } = sums;
  if (start == null || end == null) {
    return totals;
  }
  return {
    ...totals,
    billing_period_start: start,
    billing_period_end: end,
  };
};
