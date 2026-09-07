import ResourceCountBreakdown from "components/ResourceCountBreakdown";
import { useBreakdownBy } from "hooks/useBreakdownBy";
import { useVirtualTagExtraBreakdowns } from "hooks/useVirtualTagExtraBreakdowns";
import ResourcesCountBreakdownService from "services/ResourcesCountBreakdownService";
import { DAILY_RESOURCE_COUNT_BREAKDOWN_BY_PARAMETER_NAME } from "urls";

const ResourceCountBreakdownContainer = ({ requestParams }) => {
  const { useGet } = ResourcesCountBreakdownService();
  const extraBreakdowns = useVirtualTagExtraBreakdowns();

  const [{ value: breakdownByValue }, onBreakdownByChange] = useBreakdownBy({
    queryParamName: DAILY_RESOURCE_COUNT_BREAKDOWN_BY_PARAMETER_NAME,
    extraBreakdowns,
  });

  const { isGetResourceCountBreakdownLoading, data } = useGet(breakdownByValue, requestParams);

  return (
    <ResourceCountBreakdown
      resourceCountBreakdown={data}
      breakdownByValue={breakdownByValue}
      onBreakdownByChange={onBreakdownByChange}
      extraBreakdowns={extraBreakdowns}
      isLoading={isGetResourceCountBreakdownLoading}
      showTable
    />
  );
};

export default ResourceCountBreakdownContainer;
