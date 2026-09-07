import { FormattedMessage } from "react-intl";
import { GET_POOL_ALLOWED_ACTIONS, GET_RESOURCE_ALLOWED_ACTIONS } from "api/auth/actionTypes";
import IconButton from "components/IconButton";
import { useAllowedItems } from "hooks/useAllowedActions";
import { useApiData } from "hooks/useApiData";
import { useApiState } from "hooks/useApiState";
import { isEmptyArray } from "utils/arrays";
import { SCOPE_TYPES } from "utils/constants";

const allowedActionsLabelByEntityType = {
  [SCOPE_TYPES.POOL]: GET_POOL_ALLOWED_ACTIONS,
  [SCOPE_TYPES.RESOURCE]: GET_RESOURCE_ALLOWED_ACTIONS,
};

const renderActions = (items, allowedItems, isGetAllowedActionsLoading) => {
  const targetItems = isGetAllowedActionsLoading ? items : allowedItems;
  return targetItems.map((item) => (
    <IconButton
      key={item.key}
      color={item.color}
      dataTestId={item.dataTestId}
      icon={item.icon}
      onClick={item.action}
      disabled={item.disabled}
      isLoading={(isGetAllowedActionsLoading && !isEmptyArray(item.requiredActions)) || item.isLoading}
      tooltip={
        item.tooltip ?? {
          show: true,
          value: <FormattedMessage id={item.messageId} />,
        }
      }
    />
  ));
};

const TableCellActions = ({ items, entityId, entityType }) => {
  const allowedItems = useAllowedItems({ entityId, entityType, items });

  const allowedActionsLabel = allowedActionsLabelByEntityType[entityType];

  const { isLoading: isGetAllowedActionsLoading } = useApiState(allowedActionsLabel);
  const {
    apiData: { allowedActions = {} },
  } = useApiData(allowedActionsLabel || GET_POOL_ALLOWED_ACTIONS);

  // Chunked allowed_actions keep one shared loading flag. Stop spinning for a row
  // as soon as this entity id is present in the merged cache (e.g. root pool in chunk 1).
  const hasCachedActions = Boolean(entityId) && Object.prototype.hasOwnProperty.call(allowedActions, entityId);
  const showLoading = Boolean(allowedActionsLabel) && isGetAllowedActionsLoading && !hasCachedActions;

  return (
    <div
      style={{
        display: "flex",
      }}
    >
      {renderActions(items, allowedItems, showLoading)}
    </div>
  );
};

export default TableCellActions;
