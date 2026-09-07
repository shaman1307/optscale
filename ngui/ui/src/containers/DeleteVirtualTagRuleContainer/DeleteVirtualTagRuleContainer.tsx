import { useDispatch } from "react-redux";
import { deleteVirtualTagRule } from "api";
import { DELETE_VIRTUAL_TAG_RULE } from "api/restapi/actionTypes";
import DeleteEntity from "components/DeleteEntity";
import { useApiState } from "hooks/useApiState";
import { isError } from "utils/api";

const DeleteVirtualTagRuleContainer = ({ onCancel, ruleId, ruleName }) => {
  const dispatch = useDispatch();
  const { isLoading } = useApiState(DELETE_VIRTUAL_TAG_RULE);

  const onSubmit = () => {
    dispatch((_, getState) => {
      dispatch(deleteVirtualTagRule(ruleId)).then(() => {
        if (!isError(DELETE_VIRTUAL_TAG_RULE, getState())) {
          onCancel();
        }
      });
    });
  };

  return (
    <DeleteEntity
      onDelete={onSubmit}
      onCancel={onCancel}
      isLoading={isLoading}
      deleteButtonProps={{ onDelete: onSubmit }}
      message={{
        messageId: "deleteVirtualTagRuleQuestion",
        values: { name: ruleName },
      }}
    />
  );
};

export default DeleteVirtualTagRuleContainer;
