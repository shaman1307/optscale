import { describe, expect, it } from "vitest";
import { AWS_CNR, AZURE_CNR, AZURE_TENANT, ENVIRONMENT, GCP_CNR, GCP_TENANT, SNOWFLAKE } from "utils/constants";
import { groupItemsByKey } from "components/FilterComponents/selectionFilterGroups";
import { compareDataSourceVendors, getDataSourceVendor, getDataSourceVendorById } from "./dataSourceVendors";

describe("getDataSourceVendor", () => {
  it("maps gcp_cnr and gcp_tenant to the same GCP vendor family", () => {
    expect(getDataSourceVendor(GCP_CNR)).toEqual({
      id: "gcp",
      labelMessageId: "gcp",
      iconType: GCP_CNR,
    });
    expect(getDataSourceVendor(GCP_TENANT)).toEqual({
      id: "gcp",
      labelMessageId: "gcp",
      iconType: GCP_CNR,
    });
  });

  it("maps azure_cnr and azure_tenant to the same Azure vendor family", () => {
    expect(getDataSourceVendor(AZURE_CNR).id).toBe("azure");
    expect(getDataSourceVendor(AZURE_TENANT).id).toBe("azure");
  });

  it("maps other types 1:1 to short vendor ids", () => {
    expect(getDataSourceVendor(AWS_CNR).id).toBe("aws");
    expect(getDataSourceVendor(SNOWFLAKE).id).toBe("snowflake");
    expect(getDataSourceVendor(ENVIRONMENT).id).toBe("environment");
  });
});

describe("getDataSourceVendorById", () => {
  it("resolves vendor metadata from group id", () => {
    expect(getDataSourceVendorById("gcp")).toEqual({
      id: "gcp",
      labelMessageId: "gcp",
      iconType: GCP_CNR,
    });
  });
});

describe("compareDataSourceVendors", () => {
  it("orders AWS, Azure, GCP, Snowflake first", () => {
    const keys = ["snowflake", "gcp", "aws", "azure"].sort(compareDataSourceVendors);
    expect(keys).toEqual(["aws", "azure", "gcp", "snowflake"]);
  });
});

describe("vendor-family grouping for Data Source filter", () => {
  it("groups gcp_cnr and gcp_tenant accounts under one GCP group", () => {
    const items = [
      { value: "1", name: "GCP Project", type: GCP_CNR },
      { value: "2", name: "GCP Tenant", type: GCP_TENANT },
      { value: "3", name: "AWS Account", type: AWS_CNR },
    ];

    const groups = groupItemsByKey(items, (item) => getDataSourceVendor(item.type).id, compareDataSourceVendors);

    expect(groups.map(({ key }) => key)).toEqual(["aws", "gcp"]);
    expect(groups.find(({ key }) => key === "gcp")?.items.map((item) => item.value)).toEqual(["1", "2"]);
  });

  it("sorts accounts by name within a vendor group", () => {
    const items = [
      { value: "2", name: "Zebra", type: GCP_CNR },
      { value: "1", name: "Alpha", type: GCP_TENANT },
    ];

    const groups = groupItemsByKey(items, (item) => getDataSourceVendor(item.type).id, compareDataSourceVendors);

    expect(groups[0].items.map((item) => item.name)).toEqual(["Alpha", "Zebra"]);
  });
});
