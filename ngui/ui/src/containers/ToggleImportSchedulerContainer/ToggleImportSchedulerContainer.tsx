import { useDispatch } from "react-redux";
import { updateImportScheduler } from "api";
import { UPDATE_IMPORT_SCHEDULER } from "api/restapi/actionTypes";
import DeleteEntity from "components/DeleteEntity";
import { useApiState } from "hooks/useApiState";
import { useOrganizationInfo } from "hooks/useOrganizationInfo";
import { isError } from "utils/api";

type ToggleImportSchedulerContainerProps = {
  currentlyEnabled: boolean;
  closeSideModal: () => void;
};

const ToggleImportSchedulerContainer = ({ currentlyEnabled, closeSideModal }: ToggleImportSchedulerContainerProps) => {
  const dispatch = useDispatch();
  const { organizationId } = useOrganizationInfo();
  const { isLoading } = useApiState(UPDATE_IMPORT_SCHEDULER);
  const nextEnabled = currentlyEnabled !== true;

  const onSubmit = () =>
    dispatch((_, getState) => {
      dispatch(updateImportScheduler(organizationId, { enabled: nextEnabled })).then(() => {
        if (!isError(UPDATE_IMPORT_SCHEDULER, getState())) {
          closeSideModal();
        }
      });
    });

  return (
    <DeleteEntity
      onCancel={closeSideModal}
      isLoading={isLoading}
      deleteButtonProps={{
        messageId: nextEnabled ? "start" : "stop",
        color: nextEnabled ? "success" : "error",
        variant: "contained",
        onDelete: onSubmit,
      }}
      dataTestIds={{
        text: "p_toggle_import_scheduler",
        deleteButton: "btn_confirm_toggle_import_scheduler",
        cancelButton: "btn_cancel_toggle_import_scheduler",
      }}
      message={{
        messageId: nextEnabled ? "startImportSchedulersQuestion" : "stopImportSchedulersQuestion",
      }}
    />
  );
};

export default ToggleImportSchedulerContainer;
