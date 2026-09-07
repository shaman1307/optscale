import { describe, expect, it } from "vitest";
import { AWS_CNR, GCP_CNR, GCP_TENANT } from "utils/constants";
import {
  AWS_SYNTHETIC_TENANT_ID,
  buildCloudAccountsTableData,
  buildSyntheticAwsTenantDetails,
  getAwsBillingRootAccount,
  isAwsSyntheticTenantId,
} from "./buildCloudAccountsTableData";

const aws = (id, name, extra = {}) => ({
  id,
  name,
  type: AWS_CNR,
  parent_id: null,
  details: { cost: extra.cost ?? 1, resources: extra.resources ?? 1, forecast: 0, total_cost: extra.cost ?? 1 },
  ...extra,
});

describe("buildCloudAccountsTableData", () => {
  it("leaves GCP tenant nesting by parent_id unchanged", () => {
    const gcpTenant = {
      id: "tenant",
      name: "PROFITERO",
      type: GCP_TENANT,
      parent_id: null,
      details: { cost: 0, resources: 0 },
    };
    const project = {
      id: "project",
      name: "pf-orion-prod",
      type: GCP_CNR,
      parent_id: "tenant",
      details: { cost: 10, resources: 2, total_cost: 10, total_resources: 2 },
    };

    const rows = buildCloudAccountsTableData({
      cloudAccounts: [gcpTenant, project],
      childPageSize: 50,
      awsTenantName: "AWS",
    });

    expect(rows).toHaveLength(1);
    expect(rows[0].id).toBe("tenant");
    expect(rows[0].allChildrenCount).toBe(1);
    expect(rows[0].children.map((child) => child.id)).toEqual(["project"]);
    expect(rows[0].details.cost).toBe(10);
  });

  it("nests every AWS account under a synthetic tenant and keeps other clouds as roots", () => {
    const payer = aws("payer", "170107098286", { cost: 100, resources: 3 });
    const linked = aws("linked", "625245730643", { cost: 20, resources: 1, config: { linked: true } });
    const gcp = {
      id: "gcp",
      name: "PROFITERO",
      type: GCP_TENANT,
      parent_id: null,
      details: { cost: 5 },
    };

    const rows = buildCloudAccountsTableData({
      cloudAccounts: [payer, linked, gcp],
      childPageSize: 50,
      awsTenantName: "AWS",
    });

    expect(rows.map((row) => row.id)).toEqual([AWS_SYNTHETIC_TENANT_ID, "gcp"]);
    expect(rows[0].isSynthetic).toBe(true);
    expect(rows[0].type).toBe(AWS_CNR);
    expect(rows[0].name).toBe("AWS");
    expect(rows[0].children.map((child) => child.id).sort()).toEqual(["linked", "payer"]);
    expect(rows[0].allChildrenCount).toBe(2);
    expect(rows[0].details.cost).toBe(120);
    expect(rows.find((row) => row.id === "payer")).toBeUndefined();
  });

  it("does not add a synthetic tenant when there are no AWS accounts", () => {
    const rows = buildCloudAccountsTableData({
      cloudAccounts: [{ id: "env", name: "Environment", type: "environment", parent_id: null, details: {} }],
      childPageSize: 50,
      awsTenantName: "AWS",
    });

    expect(rows.map((row) => row.id)).toEqual(["env"]);
  });
});

describe("buildSyntheticAwsTenantDetails", () => {
  it("detects the synthetic tenant id", () => {
    expect(isAwsSyntheticTenantId(AWS_SYNTHETIC_TENANT_ID)).toBe(true);
    expect(isAwsSyntheticTenantId("payer")).toBe(false);
  });

  it("prefers a non-linked account as billing root", () => {
    const linked = aws("linked", "linked", { config: { linked: true } });
    const payer = aws("payer", "payer", { config: { linked: false, bucket_name: "cur" } });
    expect(getAwsBillingRootAccount([linked, payer]).id).toBe("payer");
  });

  it("builds a Snowflake-like tenant payload from AWS accounts", () => {
    const payer = aws("payer", "170107098286", {
      cost: 100,
      account_id: "170107098286",
      created_at: 100,
      last_import_at: 200,
      last_import_attempt_at: 210,
      last_import_attempt_error: null,
      config: { linked: false, bucket_name: "cur-bucket" },
      details: {
        cost: 100,
        resources: 3,
        forecast: 50,
        last_month_cost: 80,
        discovery_infos: [{ resource_type: "instance" }],
      },
    });
    const linked = aws("linked", "625245730643", {
      cost: 20,
      created_at: 50,
      config: { linked: true },
      details: { cost: 20, resources: 1, forecast: 10, last_month_cost: 15 },
    });

    const tenant = buildSyntheticAwsTenantDetails([payer, linked], "AWS");

    expect(tenant).toMatchObject({
      id: AWS_SYNTHETIC_TENANT_ID,
      name: "AWS",
      type: AWS_CNR,
      isSynthetic: true,
      account_id: "170107098286",
      created_at: 50,
      last_import_at: 200,
      billingImportDataSourceId: "payer",
      config: { linked: false, bucket_name: "cur-bucket" },
    });
    expect(tenant.details.cost).toBe(120);
    expect(tenant.details.forecast).toBe(60);
    expect(tenant.details.last_month_cost).toBe(95);
  });

  it("returns null when there are no AWS accounts", () => {
    expect(buildSyntheticAwsTenantDetails([{ id: "gcp", type: GCP_TENANT }], "AWS")).toBeNull();
  });
});
