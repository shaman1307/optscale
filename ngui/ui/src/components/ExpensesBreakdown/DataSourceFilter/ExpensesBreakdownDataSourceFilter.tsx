import { SelectionFilter } from "components/FilterComponents";
import { FILTER_CONFIGS } from "components/Resources/filterConfigs";

const cloudAccountConfig = FILTER_CONFIGS.cloudAccountId;

const ExpensesBreakdownDataSourceFilter = ({
  cloudAccountFilterValues,
  appliedFilter = { values: [] },
  onChange,
  isLoading = false,
}) => (
  <SelectionFilter
    items={cloudAccountConfig.transformers.getItems(cloudAccountFilterValues)}
    label={cloudAccountConfig.label}
    buttonIcon={cloudAccountConfig.icon}
    renderItem={cloudAccountConfig.renderItem}
    renderSelectedItem={cloudAccountConfig.renderSelectedItem}
    searchPredicate={cloudAccountConfig.searchPredicate}
    onChange={onChange}
    appliedItems={appliedFilter}
    groupBy={cloudAccountConfig.groupBy}
    renderGroupHeader={cloudAccountConfig.renderGroupHeader}
    sortGroups={cloudAccountConfig.sortGroups}
    defaultGroupsCollapsed={cloudAccountConfig.defaultGroupsCollapsed}
    isLoading={isLoading}
  />
);

export default ExpensesBreakdownDataSourceFilter;
