import { FormattedMessage } from "react-intl";
import ExpandableList from "components/ExpandableList";
import TextWithDataTestId from "components/TextWithDataTestId";
import { isEmptyArray } from "utils/arrays";
import { CELL_EMPTY_VALUE } from "utils/tables";

const formatShare = (share) => (share === 100 || share == null ? "" : ` (${share}%)`);

const groupAllocations = (items = []) => {
  const grouped = new Map();
  [...items]
    .sort((a, b) => `${a.key}:${a.value}`.localeCompare(`${b.key}:${b.value}`))
    .forEach((item) => {
      const values = grouped.get(item.key) || [];
      values.push(`${item.value}${formatShare(item.share)}`);
      grouped.set(item.key, values);
    });
  return [...grouped.entries()].map(([key, values]) => `${key}: ${values.join(", ")}`);
};

const virtualTags = ({
  headerDataTestId = "lbl_virtual_tags",
  headerMessageId = "virtualTags",
  columnSelector,
} = {}) => ({
  id: "virtual_tags",
  header: (
    <TextWithDataTestId dataTestId={headerDataTestId}>
      <FormattedMessage id={headerMessageId} />
    </TextWithDataTestId>
  ),
  columnSelector,
  accessorFn: (original) => groupAllocations(original.virtual_tags || []).join(" "),
  enableSorting: false,
  cell: ({ row: { original } }) => {
    const lines = groupAllocations(original.virtual_tags || []);
    if (isEmptyArray(lines)) {
      return CELL_EMPTY_VALUE;
    }
    return (
      <ExpandableList
        items={lines}
        render={(line) => <div key={line}>{line}</div>}
        maxRows={3}
      />
    );
  },
});

export default virtualTags;
