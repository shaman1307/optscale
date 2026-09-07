import { SelectionFilter } from "components/FilterComponents";
import { FILTER_CONFIGS } from "components/Resources/filterConfigs";

const regionConfig = FILTER_CONFIGS.region;

const ExpensesBreakdownRegionFilter = ({
  regionFilterValues,
  appliedFilter = { values: [] },
  onChange,
  isLoading = false,
}) => (
  <SelectionFilter
    items={regionConfig.transformers.getItems(regionFilterValues)}
    label={regionConfig.label}
    buttonIcon={regionConfig.icon}
    renderItem={regionConfig.renderItem}
    renderSelectedItem={regionConfig.renderSelectedItem}
    searchPredicate={regionConfig.searchPredicate}
    onChange={onChange}
    appliedItems={appliedFilter}
    groupBy={regionConfig.groupBy}
    renderGroupHeader={regionConfig.renderGroupHeader}
    sortGroups={regionConfig.sortGroups}
    defaultGroupsCollapsed={regionConfig.defaultGroupsCollapsed}
    isLoading={isLoading}
  />
);

export default ExpensesBreakdownRegionFilter;
