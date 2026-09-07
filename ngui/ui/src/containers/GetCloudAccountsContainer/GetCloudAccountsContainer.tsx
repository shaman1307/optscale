import { useEffect } from "react";
import { useDispatch } from "react-redux";
import { getImportScheduler, getPool } from "api";
import { GET_IMPORT_SCHEDULER, GET_POOL, UPDATE_IMPORT_SCHEDULER } from "api/restapi/actionTypes";
import CloudAccountsOverview from "components/CloudAccountsOverview";
import { useAllDataSources } from "hooks/coreData/useAllDataSources";
import { useApiData } from "hooks/useApiData";
import { useApiState } from "hooks/useApiState";
import { useOrganizationInfo } from "hooks/useOrganizationInfo";

const GetCloudAccountsContainer = () => {
  const dataSources = useAllDataSources();

  const { organizationPoolId, organizationId } = useOrganizationInfo();

  const dispatch = useDispatch();

  const {
    apiData: { pool: { limit: organizationLimit = 0 } = {} },
  } = useApiData(GET_POOL);

  const {
    apiData: { enabled: incrementalSchedulerEnabled },
  } = useApiData(GET_IMPORT_SCHEDULER);

  const { isLoading: isGetPoolLoading, shouldInvoke: shouldInvokeGetPool } = useApiState(GET_POOL, {
    poolId: organizationPoolId,
  });

  const { isLoading: isGetSchedulerLoading, shouldInvoke: shouldInvokeGetScheduler } = useApiState(GET_IMPORT_SCHEDULER, {
    organizationId,
  });

  const { isLoading: isUpdatingIncrementalScheduler } = useApiState(UPDATE_IMPORT_SCHEDULER);

  useEffect(() => {
    if (organizationPoolId && shouldInvokeGetPool) {
      dispatch(getPool(organizationPoolId));
    }
  }, [shouldInvokeGetPool, dispatch, organizationPoolId]);

  useEffect(() => {
    if (organizationId && shouldInvokeGetScheduler) {
      dispatch(getImportScheduler(organizationId));
    }
  }, [dispatch, organizationId, shouldInvokeGetScheduler]);

  return (
    <CloudAccountsOverview
      isLoading={isGetPoolLoading}
      cloudAccounts={dataSources}
      organizationLimit={organizationLimit}
      incrementalSchedulerEnabled={incrementalSchedulerEnabled}
      isIncrementalSchedulerLoading={isGetSchedulerLoading}
      isUpdatingIncrementalScheduler={isUpdatingIncrementalScheduler}
    />
  );
};

export default GetCloudAccountsContainer;
