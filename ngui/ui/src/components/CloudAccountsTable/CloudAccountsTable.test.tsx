import { createRoot } from "react-dom/client";
import TestProvider from "tests/TestProvider";
import { GCP_CNR, GCP_TENANT } from "utils/constants";
import CloudAccountsTable from "./CloudAccountsTable";

it("renders without crashing", () => {
  const div = document.createElement("div");
  const root = createRoot(div);
  root.render(
    <TestProvider>
      <CloudAccountsTable />
    </TestProvider>
  );
  root.unmount();
});

it("renders mixed-length nested project names without crashing", () => {
  const div = document.createElement("div");
  const root = createRoot(div);
  root.render(
    <TestProvider>
      <CloudAccountsTable
        cloudAccounts={[
          {
            id: "tenant",
            name: "PROFITERO",
            type: GCP_TENANT,
            parent_id: null,
            details: { cost: 0, resources: 0 },
          },
          {
            id: "short",
            name: "Support",
            type: GCP_CNR,
            parent_id: "tenant",
            details: { cost: 10, resources: 1, cost_mismatch: true },
          },
          {
            id: "long",
            name: "Profitero (112479942571216894486)",
            type: GCP_CNR,
            parent_id: "tenant",
            details: { cost: 20, resources: 2 },
          },
        ]}
      />
    </TestProvider>
  );
  root.unmount();
});
