import { useEffect } from "react";
import { useDispatch } from "react-redux";
import { getResourceCountBreakdown } from "api";
import { GET_RESOURCE_COUNT_BREAKDOWN } from "api/restapi/actionTypes";
import { useApiData } from "hooks/useApiData";
import { useApiState } from "hooks/useApiState";
import { useOrganizationInfo } from "hooks/useOrganizationInfo";
import { toDateRangeApiParams } from "utils/costPeriod";
import { mapAvailableFilterKeys } from "./AvailableFiltersService";

const getParams = (breakdownBy, filterParams) => ({
  ...toDateRangeApiParams(filterParams),
  ...mapAvailableFilterKeys(filterParams),
  breakdown_by: breakdownBy,
});

export const useGet = (breakdownBy, params) => {
  const dispatch = useDispatch();
  const { organizationId } = useOrganizationInfo();
  const { apiData: data } = useApiData(GET_RESOURCE_COUNT_BREAKDOWN);

  const { isLoading, shouldInvoke } = useApiState(GET_RESOURCE_COUNT_BREAKDOWN, {
    organizationId,
    ...getParams(breakdownBy, params),
  });

  useEffect(() => {
    if (shouldInvoke) {
      dispatch(getResourceCountBreakdown(organizationId, getParams(breakdownBy, params)));
    }
  }, [breakdownBy, dispatch, organizationId, params, shouldInvoke]);

  return { isGetResourceCountBreakdownLoading: isLoading, data };
};

function ResourcesCountBreakdownService() {
  return { useGet };
}

export default ResourcesCountBreakdownService;
