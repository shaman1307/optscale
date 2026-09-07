import Typography from "@mui/material/Typography";
import { FormattedMessage } from "react-intl";
import CloudLabel from "components/CloudLabel";
import SubTitle from "components/SubTitle";
import { useAllDataSources } from "hooks/coreData/useAllDataSources";
import { isEmptyArray } from "utils/arrays";

type ChildrenListProps = {
  parentId?: string;
  /** When set, selects children instead of filtering by parent_id (e.g. synthetic AWS tenant). */
  filterChildren?: (dataSource: { id: string; name: string; type: string; parent_id?: string | null }) => boolean;
};

const ChildrenList = ({ parentId, filterChildren }: ChildrenListProps) => {
  const dataSources = useAllDataSources();

  const childDataSources = (
    filterChildren
      ? dataSources.filter(filterChildren)
      : dataSources.filter(({ parent_id: accountParentId }) => accountParentId === parentId)
  ).sort((left, right) => String(left.name || "").localeCompare(String(right.name || "")));

  return (
    <>
      <SubTitle>
        <FormattedMessage id="childDataSources" />
      </SubTitle>
      {isEmptyArray(childDataSources) ? (
        <Typography>
          <FormattedMessage id="noChildDataSourcesDiscovered" />
        </Typography>
      ) : (
        childDataSources.map(({ id, name, type }) => <CloudLabel key={id} id={id} name={name} type={type} />)
      )}
    </>
  );
};

export default ChildrenList;
