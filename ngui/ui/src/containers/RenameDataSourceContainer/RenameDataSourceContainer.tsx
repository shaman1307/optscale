import { GET_AVAILABLE_FILTERS } from "api/restapi/actionTypes";
import RenameDataSourceForm from "components/forms/RenameDataSourceForm";
import { FormValues } from "components/forms/RenameDataSourceForm/types";
import { useUpdateDataSourceMutation } from "graphql/__generated__/hooks/restapi";
import { useOrganizationInfo } from "hooks/useOrganizationInfo";
import { useRefetchApis } from "hooks/useRefetchApis";
import { isAwsSyntheticTenantId, writeAwsSyntheticTenantName } from "utils/dataSources";

const RenameDataSourceContainer = ({ id, name, closeSideModal }) => {
  const { organizationId } = useOrganizationInfo();
  const [updateDataSource, { loading }] = useUpdateDataSourceMutation();
  const isSyntheticAwsTenant = isAwsSyntheticTenantId(id);

  const refetch = useRefetchApis();

  const onSubmit = (formData: FormValues) => {
    if (isSyntheticAwsTenant) {
      if (organizationId) {
        writeAwsSyntheticTenantName(organizationId, formData.name);
      }
      closeSideModal();
      return;
    }

    updateDataSource({
      variables: {
        dataSourceId: id,
        params: {
          name: formData.name,
        },
      },
    }).then(() => {
      refetch([GET_AVAILABLE_FILTERS]);
      closeSideModal();
    });
  };

  return <RenameDataSourceForm name={name} onSubmit={onSubmit} onCancel={closeSideModal} isLoading={loading && !isSyntheticAwsTenant} />;
};

export default RenameDataSourceContainer;
