import { Navigate } from "react-router-dom";
import CloudAccountDetails from "components/CloudAccountDetails";
import { useDataSourceQuery } from "graphql/__generated__/hooks/restapi";
import { useAllDataSources } from "hooks/coreData/useAllDataSources";
import { useAwsSyntheticTenantName } from "hooks/useAwsSyntheticTenantName";
import { CLOUD_ACCOUNTS } from "urls";
import { buildSyntheticAwsTenantDetails, isAwsSyntheticTenantId } from "utils/dataSources";

type CloudAccountDetailsContainerProps = {
  cloudAccountId: string;
};

const CloudAccountDetailsContainer = ({ cloudAccountId }: CloudAccountDetailsContainerProps) => {
  const dataSources = useAllDataSources();
  const awsTenantName = useAwsSyntheticTenantName();
  const isSyntheticAwsTenant = isAwsSyntheticTenantId(cloudAccountId);

  const { loading, data } = useDataSourceQuery({
    variables: {
      dataSourceId: cloudAccountId,
      requestParams: {
        details: true,
      },
    },
    skip: isSyntheticAwsTenant,
  });

  if (isSyntheticAwsTenant) {
    const syntheticData = buildSyntheticAwsTenantDetails(dataSources, awsTenantName);
    if (!syntheticData) {
      // Cache may still be empty on first paint; once loaded with no AWS accounts, leave the page.
      return dataSources.length ? <Navigate to={CLOUD_ACCOUNTS} replace /> : <CloudAccountDetails isLoading />;
    }
    return <CloudAccountDetails data={syntheticData} />;
  }

  return <CloudAccountDetails data={data?.dataSource} isLoading={loading} />;
};

export default CloudAccountDetailsContainer;
