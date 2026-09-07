import CategoryOutlinedIcon from "@mui/icons-material/CategoryOutlined";
import { FormattedMessage, useIntl } from "react-intl";
import CloudTypeIcon from "components/CloudTypeIcon";
import { SelectionFilter } from "components/FilterComponents";
import { compareDataSourceVendors, getDataSourceVendor } from "utils/dataSourceVendors";

const buildVendorItems = (cloudAccounts, intl) => {
  const byId = new Map();
  for (const account of cloudAccounts || []) {
    if (!account?.type) {
      continue;
    }
    const vendor = getDataSourceVendor(account.type);
    if (byId.has(vendor.id)) {
      continue;
    }
    byId.set(vendor.id, {
      id: vendor.id,
      value: vendor.id,
      name: intl.formatMessage({ id: vendor.labelMessageId }),
      type: vendor.iconType,
    });
  }
  return Array.from(byId.values()).sort((left, right) => compareDataSourceVendors(left.id, right.id));
};

const ExpensesBreakdownVendorFilter = ({
  cloudAccountFilterValues,
  appliedFilter = { values: [] },
  onChange,
  isLoading = false,
}) => {
  const intl = useIntl();
  const items = buildVendorItems(cloudAccountFilterValues, intl);

  return (
    <SelectionFilter
      items={items}
      label={<FormattedMessage id="vendor" />}
      buttonIcon={<CategoryOutlinedIcon />}
      renderItem={(item) => (
        <>
          <CloudTypeIcon type={item.type} hasRightMargin />
          {item.name}
        </>
      )}
      renderSelectedItem={(item) => item.name}
      searchPredicate={(item, query) => item.name.toLowerCase().includes(query.toLowerCase())}
      onChange={onChange}
      appliedItems={appliedFilter}
      isLoading={isLoading}
    />
  );
};

export default ExpensesBreakdownVendorFilter;
