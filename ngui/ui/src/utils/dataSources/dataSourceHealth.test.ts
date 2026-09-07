import { describe, expect, it } from "vitest";
import { hasDataSourceHealthIssue, hasStageTargetCostMismatch } from "./dataSourceHealth";

describe("hasStageTargetCostMismatch", () => {
  it("is false when Stage and Target differ by at most $1", () => {
    expect(hasStageTargetCostMismatch(10.001, 10.004)).toBe(false);
    expect(hasStageTargetCostMismatch(10, 11)).toBe(false);
    expect(hasStageTargetCostMismatch(10.01, 10.02)).toBe(false);
  });

  it("is true when displayed Stage and Target differ by more than $1", () => {
    expect(hasStageTargetCostMismatch(10, 11.01)).toBe(true);
    expect(hasStageTargetCostMismatch(10, 16)).toBe(true);
  });

  it("is false when a side is missing", () => {
    expect(hasStageTargetCostMismatch(10, null)).toBe(false);
    expect(hasStageTargetCostMismatch(undefined, 10)).toBe(false);
  });
});

describe("hasDataSourceHealthIssue", () => {
  it("is true when Duplicate groups is not zero", () => {
    expect(hasDataSourceHealthIssue({ details: { duplicate_groups: 2 } })).toBe(true);
  });

  it("is true when Target cost differs from Stage cost", () => {
    expect(hasDataSourceHealthIssue({ details: { cost_mismatch: true } })).toBe(true);
  });

  it("is false for a healthy project", () => {
    expect(hasDataSourceHealthIssue({ details: { duplicate_groups: 0, cost_mismatch: false } })).toBe(false);
    expect(hasDataSourceHealthIssue({})).toBe(false);
  });
});
