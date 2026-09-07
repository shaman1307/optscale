import { describe, expect, it } from "vitest";
import { summarizeChildrenDetails } from "./summarizeTenantChildren";

describe("summarizeChildrenDetails", () => {
  it("returns empty object for empty children", () => {
    expect(summarizeChildrenDetails([])).toEqual({});
  });

  it("sums costs and takes min/max billing period across children", () => {
    expect(
      summarizeChildrenDetails([
        {
          details: {
            cost: 10,
            resources: 1,
            forecast: 2,
            last_month_cost: 3,
            total_cost: 100,
            total_resources: 4,
            billing_period_start: 200,
            billing_period_end: 400,
          },
        },
        {
          details: {
            cost: 5,
            resources: 2,
            forecast: 1,
            last_month_cost: 1,
            total_cost: 50,
            total_resources: 3,
            billing_period_start: 100,
            billing_period_end: 500,
          },
        },
        {
          details: {
            cost: 1,
            // no billing period — ignored for min/max
          },
        },
      ])
    ).toEqual({
      resources: 3,
      cost: 16,
      forecast: 3,
      last_month_cost: 4,
      total_cost: 150,
      total_resources: 7,
      billing_period_start: 100,
      billing_period_end: 500,
    });
  });

  it("omits billing period when no child has one", () => {
    expect(
      summarizeChildrenDetails([
        {
          details: { cost: 1, resources: 1, forecast: 0, last_month_cost: 0, total_cost: 1, total_resources: 1 },
        },
      ])
    ).toEqual({
      resources: 1,
      cost: 1,
      forecast: 0,
      last_month_cost: 0,
      total_cost: 1,
      total_resources: 1,
    });
  });
});
