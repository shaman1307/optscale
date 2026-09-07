import { useCallback, useEffect, useMemo, useState } from "react";
import { useDispatch } from "react-redux";
import { getVirtualTags as getVirtualTagsAction } from "api";
import { GET_VIRTUAL_TAGS } from "api/restapi/actionTypes";
import VirtualTags from "components/VirtualTags";
import { useAllDataSources } from "hooks/coreData/useAllDataSources";
import { useApiData } from "hooks/useApiData";
import { useApiState } from "hooks/useApiState";
import { useOrganizationInfo } from "hooks/useOrganizationInfo";
import useVirtualTagQuarterOptions from "hooks/useVirtualTagQuarterOptions";
import { currentQuarter } from "utils/costPeriod";

const VirtualTagsContainer = () => {
  const dispatch = useDispatch();
  const { organizationId } = useOrganizationInfo();
  const dataSources = useAllDataSources();
  const [filters, setFilters] = useState({
    quarter: currentQuarter(),
    names: [] as string[],
    cloud_account_ids: [] as string[],
  });
  const {
    quarterOptions,
    extraQuarters,
    dbQuarters,
    addQuarter,
    removeQuarter,
    isSaving,
  } = useVirtualTagQuarterOptions(filters.quarter);

  const queryParams = useMemo(() => {
    const params = { quarter: filters.quarter };
    if (filters.cloud_account_ids.length > 0) {
      params.cloud_account_id = filters.cloud_account_ids;
    }
    return params;
  }, [filters.cloud_account_ids, filters.quarter]);

  const requestParams = useMemo(
    () => ({ organizationId, ...queryParams }),
    [organizationId, queryParams]
  );

  const {
    apiData: { virtualTags = [] },
  } = useApiData(GET_VIRTUAL_TAGS, { virtualTags: [], quarters: [] });

  const { isLoading, shouldInvoke } = useApiState(GET_VIRTUAL_TAGS, requestParams);

  const getVirtualTags = useCallback(
    () => dispatch(getVirtualTagsAction(organizationId, queryParams)),
    [dispatch, organizationId, queryParams]
  );

  useEffect(() => {
    if (shouldInvoke) {
      getVirtualTags();
    }
  }, [getVirtualTags, shouldInvoke]);

  return (
    <VirtualTags
      isLoading={isLoading}
      virtualTags={virtualTags}
      filters={filters}
      onFiltersChange={setFilters}
      dataSources={dataSources}
      quarterOptions={quarterOptions}
      extraQuarters={extraQuarters}
      dbQuarters={dbQuarters}
      onAddQuarter={addQuarter}
      onRemoveQuarter={removeQuarter}
      isSavingQuarters={isSaving}
    />
  );
};

export default VirtualTagsContainer;
