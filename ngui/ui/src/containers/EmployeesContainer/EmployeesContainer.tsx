import Employees from "components/Employees";
import { useIsAllowed } from "hooks/useAllowedActions";
import EmployeesService from "services/EmployeesService";

const EmployeesContainer = () => {
  const { useGet } = EmployeesService();

  const { isLoading, employees } = useGet();
  const showSsoSettings = useIsAllowed({ requiredActions: ["EDIT_PARTNER"] });

  return <Employees employees={employees} isLoading={isLoading} showSsoSettings={showSsoSettings} />;
};

export default EmployeesContainer;
