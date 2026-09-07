import { useDispatch } from "react-redux";
import { useNavigate } from "react-router-dom";
import { createVirtualTag } from "api";
import { CREATE_VIRTUAL_TAG } from "api/restapi/actionTypes";
import VirtualTagForm from "components/forms/VirtualTagForm";
import { useApiState } from "hooks/useApiState";
import { useOrganizationInfo } from "hooks/useOrganizationInfo";
import useVirtualTagQuarterOptions from "hooks/useVirtualTagQuarterOptions";
import { VIRTUAL_TAGS } from "urls";
import { isError } from "utils/api";
import { currentQuarter } from "utils/costPeriod";

const CreateVirtualTagFormContainer = () => {
  const dispatch = useDispatch();
  const navigate = useNavigate();
  const { organizationId } = useOrganizationInfo();
  const { isLoading } = useApiState(CREATE_VIRTUAL_TAG);
  const { quarterOptions } = useVirtualTagQuarterOptions(currentQuarter());

  const onSubmit = (formData) => {
    dispatch((_, getState) => {
      dispatch(
        createVirtualTag(organizationId, {
          key: formData.key,
          name: formData.name,
          mode: formData.mode,
          quarter: formData.quarter,
          source_tag_key: formData.mode === "extract" ? formData.source_tag_key : undefined,
        })
      ).then(() => {
        if (!isError(CREATE_VIRTUAL_TAG, getState())) {
          navigate(VIRTUAL_TAGS);
        }
      });
    });
  };

  return (
    <VirtualTagForm
      defaultValues={{ key: "", name: "", mode: "assignment", source_tag_key: "", quarter: currentQuarter() }}
      onSubmit={onSubmit}
      onCancel={() => navigate(VIRTUAL_TAGS)}
      isLoading={isLoading}
      quarters={quarterOptions}
    />
  );
};

export default CreateVirtualTagFormContainer;
