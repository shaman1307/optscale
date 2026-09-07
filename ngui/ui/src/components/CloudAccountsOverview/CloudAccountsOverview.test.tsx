import { createRoot } from "react-dom/client";
import TestProvider from "tests/TestProvider";
import CloudAccountsOverview from "./CloudAccountsOverview";

const renderOverview = (props = {}) => {
  const div = document.createElement("div");
  const root = createRoot(div);
  root.render(
    <TestProvider>
      <CloudAccountsOverview cloudAccounts={[]} organizationLimit={0} {...props} />
    </TestProvider>
  );
  root.unmount();
};

it("renders without crashing", () => {
  renderOverview();
});

it("renders stop scheduler control when incremental imports are running", () => {
  renderOverview({
    incrementalSchedulerEnabled: true,
  });
});

it("renders start scheduler control when incremental imports are paused", () => {
  renderOverview({
    incrementalSchedulerEnabled: false,
  });
});
