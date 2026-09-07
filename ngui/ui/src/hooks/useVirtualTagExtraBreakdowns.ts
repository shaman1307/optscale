import { useEffect, useMemo } from "react";
import { useDispatch } from "react-redux";
import { getVirtualTags } from "api";
import { GET_VIRTUAL_TAGS } from "api/restapi/actionTypes";
import { useApiData } from "hooks/useApiData";
import { useApiState } from "hooks/useApiState";
import { useOrganizationInfo } from "hooks/useOrganizationInfo";
import { currentQuarter } from "utils/costPeriod";

export const useVirtualTagExtraBreakdowns = () => {
  const dispatch = useDispatch();
  const { organizationId } = useOrganizationInfo();
  const quarter = currentQuarter();
  const requestParams = useMemo(() => ({ organizationId, quarter }), [organizationId, quarter]);
  const {
    apiData: { virtualTags = [] },
  } = useApiData(GET_VIRTUAL_TAGS, { virtualTags: [] });
  const { shouldInvoke } = useApiState(GET_VIRTUAL_TAGS, requestParams);

  useEffect(() => {
    if (shouldInvoke) {
      dispatch(getVirtualTags(organizationId, { quarter }));
    }
  }, [dispatch, organizationId, quarter, shouldInvoke]);

  return useMemo(
    () =>
      virtualTags.map((item) => ({
        value: `virtual_tag:${item.key}`,
        name: item.name || item.key,
      })),
    [virtualTags]
  );
};
