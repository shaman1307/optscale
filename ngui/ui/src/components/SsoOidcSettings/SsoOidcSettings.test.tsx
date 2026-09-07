import { createRoot } from "react-dom/client";
import TestProvider from "tests/TestProvider";
import SsoOidcSettings from "./SsoOidcSettings";

it("renders without crashing", () => {
  const div = document.createElement("div");
  const root = createRoot(div);
  root.render(
    <TestProvider>
      <SsoOidcSettings />
    </TestProvider>
  );
  root.unmount();
});
