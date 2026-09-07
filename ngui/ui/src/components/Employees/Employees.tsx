import ActionBar from "components/ActionBar";
import EmployeesTable from "components/EmployeesTable";
import PageContentWrapper from "components/PageContentWrapper";
import SsoOidcSettings from "components/SsoOidcSettings";
import TabsWrapper from "components/TabsWrapper";

const TABS = Object.freeze({
  USERS: "users",
  SSO_SETTINGS: "ssoSettings",
});

const Employees = ({ employees, isLoading, showSsoSettings = false }) => {
  const tabs = [
    {
      title: TABS.USERS,
      dataTestId: "tab_users",
      node: <EmployeesTable employees={employees} isLoading={isLoading} />,
    },
    {
      title: TABS.SSO_SETTINGS,
      dataTestId: "tab_sso_settings",
      node: <SsoOidcSettings />,
      renderCondition: () => showSsoSettings,
    },
  ];

  return (
    <>
      <ActionBar
        data={{
          title: {
            messageId: "users",
            dataTestId: "lbl_users",
          },
        }}
      />
      <PageContentWrapper>
        {showSsoSettings ? (
          <TabsWrapper
            tabsProps={{
              tabs,
              defaultTab: TABS.USERS,
              name: "user-management",
            }}
          />
        ) : (
          <EmployeesTable employees={employees} isLoading={isLoading} />
        )}
      </PageContentWrapper>
    </>
  );
};

export default Employees;
