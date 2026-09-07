import { describe, expect, it } from "vitest";
import {
  buildPoolFilterTree,
  collectSelectableTreeValues,
  getOnlySubpoolValues,
  groupItemsByKey,
  toggleGroupSelection,
} from "./selectionFilterGroups";

describe("groupItemsByKey", () => {
  it("groups items and sorts groups and names", () => {
    const groups = groupItemsByKey(
      [
        { value: "b2", name: "Beta", type: "gcp" },
        { value: "a1", name: "Alpha", type: "aws" },
        { value: "b1", name: "Alpha", type: "gcp" },
      ],
      (item) => item.type,
      (a, b) => a.localeCompare(b)
    );

    expect(groups.map(({ key }) => key)).toEqual(["aws", "gcp"]);
    expect(groups[1].items.map((item) => item.value)).toEqual(["b1", "b2"]);
  });
});

describe("toggleGroupSelection", () => {
  it("selects all group values without duplicating existing ones", () => {
    expect(toggleGroupSelection(["a1", "x"], ["a1", "a2"], true)).toEqual(["a1", "x", "a2"]);
  });

  it("deselects only values belonging to the group", () => {
    expect(toggleGroupSelection(["a1", "a2", "x"], ["a1", "a2"], false)).toEqual(["x"]);
  });
});

describe("getOnlySubpoolValues", () => {
  it("selects nested service pools and skips org root and project pools", () => {
    const items = [
      { value: "org", name: "Profitero", parent_id: null },
      { value: "project", name: "pf-orion-prod", parent_id: "org" },
      { value: "service-a", name: "pf-orion-prod - logging", parent_id: "project" },
      { value: "service-b", name: "pf-orion-prod - airflow", parent_id: "project" },
      { value: "lonely-budget", name: "misc-budget", parent_id: "org" },
    ];

    expect(getOnlySubpoolValues(items).sort()).toEqual(["service-a", "service-b"]);
  });
});

describe("buildPoolFilterTree", () => {
  it("nests pools under type groups like the Pools tab", () => {
    const tree = buildPoolFilterTree([
      { id: "org", name: "Profitero", purpose: "business_unit", parent_id: null },
      { id: "project", name: "pf-orion-prod", purpose: "budget", parent_id: "org" },
      { id: "service", name: "pf-orion-prod - logging", purpose: "asset_pool", parent_id: "project" },
    ]);

    expect(tree).toHaveLength(1);
    expect(tree[0].item.value).toBe("org");
    expect(tree[0].children[0].item.isPoolTypeGroup).toBe(true);
    expect(tree[0].children[0].item.purpose).toBe("budget");
    expect(tree[0].children[0].children[0].item.value).toBe("project");
    expect(tree[0].children[0].children[0].children[0].item.purpose).toBe("asset_pool");
    expect(tree[0].children[0].children[0].children[0].children[0].item.value).toBe("service");
    expect(collectSelectableTreeValues(tree[0]).sort()).toEqual(["org", "project", "service"]);
  });
});
