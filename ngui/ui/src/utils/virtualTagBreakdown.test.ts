import { describe, expect, it } from "vitest";
import {
  getVirtualTagKeyFromFilterBy,
  isVirtualTagBreakdown,
  mapBreakdownExpensesToFormatted,
} from "./virtualTagBreakdown";

describe("virtualTagBreakdown", () => {
  it("detects virtual tag filterBy values", () => {
    expect(isVirtualTagBreakdown("virtual_tag:PRODUCT")).toBe(true);
    expect(isVirtualTagBreakdown("cloud")).toBe(false);
    expect(getVirtualTagKeyFromFilterBy("virtual_tag:PRODUCT")).toBe("PRODUCT");
  });

  it("maps breakdown_expenses payload to Cost Explorer series", () => {
    const mapped = mapBreakdownExpensesToFormatted({
      total: 12,
      previous_total: 4,
      breakdown: {
        "1700000000": {
          SNS: { id: "SNS", name: "SNS", cost: 10 },
        },
      },
      counts: {
        SNS: { id: "SNS", name: "SNS", total: 10, previous_total: 2 },
      },
    });

    expect(mapped.total).toBe(12);
    expect(mapped.previousTotal).toBe(4);
    expect(mapped.breakdown["1700000000"]).toEqual([
      expect.objectContaining({ id: "SNS", name: "SNS", expense: 10 }),
    ]);
    expect(mapped.filteredBreakdown).toEqual([
      expect.objectContaining({ id: "SNS", name: "SNS", total: 10, previous_total: 2 }),
    ]);
  });
});
