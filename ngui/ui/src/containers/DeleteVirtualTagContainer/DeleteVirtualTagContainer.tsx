import { useDispatch } from "react-redux";
import { deleteVirtualTag } from "api";
import { DELETE_VIRTUAL_TAG } from "api/restapi/actionTypes";
import DeleteEntity from "components/DeleteEntity";
import { useApiState } from "hooks/useApiState";
import { isError } from "utils/api";

const DeleteVirtualTagContainer = ({ onCancel, virtualTagId, virtualTagName }) => {
  const dispatch = useDispatch();
  const { isLoading } = useApiState(DELETE_VIRTUAL_TAG);

  const onSubmit = () => {
    dispatch((_, getState) => {
      dispatch(deleteVirtualTag(virtualTagId)).then(() => {
        if (!isError(DELETE_VIRTUAL_TAG, getState())) {
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
        messageId: "deleteVirtualTagQuestion",
        values: { name: virtualTagName },
      }}
    />
  );
};

export default DeleteVirtualTagContainer;
