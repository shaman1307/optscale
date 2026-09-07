import { CREATE_ORGANIZATION_CONSTRAINT, UPDATE_ORGANIZATION_CONSTRAINT } from "api/restapi/actionTypes";
import ButtonLoader from "components/ButtonLoader";
import { useApiState } from "hooks/useApiState";
import { useOrganizationActionRestrictions } from "hooks/useOrganizationActionRestrictions";
import AvailableFiltersService from "services/AvailableFiltersService";

const SubmitButton = ({ isEdit = false, isSubmitLoading = false }) => {
  const { isRestricted, restrictionReasonMessage } = useOrganizationActionRestrictions();

  const { useIsLoading: useIsAvailableFiltersLoading } = AvailableFiltersService();

  const isAvailableFiltersLoading = useIsAvailableFiltersLoading();

  const { isLoading: isCreateOrganizationConstraintLoading } = useApiState(CREATE_ORGANIZATION_CONSTRAINT);
  const { isLoading: isUpdateOrganizationConstraintLoading } = useApiState(UPDATE_ORGANIZATION_CONSTRAINT);

  const isMutationLoading = isEdit ? isUpdateOrganizationConstraintLoading : isCreateOrganizationConstraintLoading;

  return (
    <ButtonLoader
      variant="contained"
      messageId="save"
      color="primary"
      type="submit"
      isLoading={isAvailableFiltersLoading || isMutationLoading || isSubmitLoading}
      disabled={isRestricted}
      tooltip={{
        show: isRestricted,
        value: restrictionReasonMessage,
      }}
      dataTestId={isEdit ? "btn_save" : "btn_create"}
    />
  );
};

export default SubmitButton;
