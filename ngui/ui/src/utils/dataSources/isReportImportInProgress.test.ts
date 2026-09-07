import { describe, expect, it } from "vitest";
import { getCurrentUTCTimeInSec } from "utils/datetime";
import { isReportImportInProgress } from "./isReportImportInProgress";

describe("isReportImportInProgress", () => {
  it("blocks a reimport for a recently queued import", () => {
    expect(isReportImportInProgress([{ state: "scheduled", created_at: getCurrentUTCTimeInSec() }])).toBe(true);
  });

  it("does not block a reimport for a stale queued import", () => {
    expect(isReportImportInProgress([{ state: "scheduled", created_at: 0 }])).toBe(false);
  });

  it("blocks a reimport for a recently updated import in progress", () => {
    expect(isReportImportInProgress([{ state: "in_progress", updated_at: getCurrentUTCTimeInSec() }])).toBe(true);
  });

  it("does not block a reimport for a stale import in progress", () => {
    expect(isReportImportInProgress([{ state: "in_progress", updated_at: 0 }])).toBe(false);
  });
});
