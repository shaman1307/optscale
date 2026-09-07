import Box from "@mui/material/Box";
import { type SxProps, type Theme } from "@mui/material/styles";
import { FILTER_CONFIGS } from "components/Resources/filterConfigs";
import SelectionFilter from "./SelectionFilter";

const CLOUD_ACCOUNT_FILTER = FILTER_CONFIGS.cloudAccountId;

type DataSourceItem = {
  id?: string | null;
  name?: string | null;
  type?: string | null;
};

type DataSourceSelectionFilterProps = {
  dataSources?: Array<DataSourceItem | null>;
  values: string[];
  onChange: (values: string[]) => void;
  dataTestId?: string;
  /** Radio instead of checkboxes — one source (form fields). */
  single?: boolean;
  error?: boolean;
  buttonSx?: SxProps<Theme>;
};

/** Same Data source picker as Resources. */
const DataSourceSelectionFilter = ({
  dataSources = [],
  values,
  onChange,
  dataTestId,
  single = false,
  error = false,
  buttonSx,
}: DataSourceSelectionFilterProps) => (
  <Box data-test-id={dataTestId} sx={{ flex: "0 0 auto" }}>
    <SelectionFilter
      items={CLOUD_ACCOUNT_FILTER.transformers.getItems(dataSources)}
      label={CLOUD_ACCOUNT_FILTER.label}
      buttonIcon={CLOUD_ACCOUNT_FILTER.icon}
      renderItem={CLOUD_ACCOUNT_FILTER.renderItem}
      renderSelectedItem={CLOUD_ACCOUNT_FILTER.renderSelectedItem}
      searchPredicate={CLOUD_ACCOUNT_FILTER.searchPredicate}
      groupBy={CLOUD_ACCOUNT_FILTER.groupBy}
      renderGroupHeader={CLOUD_ACCOUNT_FILTER.renderGroupHeader}
      sortGroups={CLOUD_ACCOUNT_FILTER.sortGroups}
      defaultGroupsCollapsed={CLOUD_ACCOUNT_FILTER.defaultGroupsCollapsed}
      appliedItems={{ values }}
      onChange={(selected) => onChange(selected.values)}
      single={single}
      error={error}
      buttonSx={buttonSx}
    />
  </Box>
);

export default DataSourceSelectionFilter;
