import { useEffect } from "react";
import { useDispatch } from "react-redux";
import { useNavigate, useParams } from "react-router-dom";
import { getVirtualTag, updateVirtualTag } from "api";
import { GET_VIRTUAL_TAG, UPDATE_VIRTUAL_TAG } from "api/restapi/actionTypes";
import VirtualTagForm from "components/forms/VirtualTagForm";
import { useApiData } from "hooks/useApiData";
import { useApiState } from "hooks/useApiState";
import useVirtualTagQuarterOptions from "hooks/useVirtualTagQuarterOptions";
import { getVirtualTagUrl } from "urls";
import { isError } from "utils/api";

const EditVirtualTagFormContainer = () => {
  const { virtualTagId } = useParams();
  const dispatch = useDispatch();
  const navigate = useNavigate();

  const {
    apiData: { virtualTag = {} },
  } = useApiData(GET_VIRTUAL_TAG, { virtualTag: {} });
  const { isLoading: isSaving } = useApiState(UPDATE_VIRTUAL_TAG);
  const { quarterOptions } = useVirtualTagQuarterOptions(virtualTag.quarter);

  useEffect(() => {
    dispatch(getVirtualTag(virtualTagId));
  }, [dispatch, virtualTagId]);

  const redirect = () => navigate(getVirtualTagUrl(virtualTagId));

  const onSubmit = (formData) => {
    dispatch((_, getState) => {
      dispatch(
        updateVirtualTag(virtualTagId, {
          key: formData.key,
          name: formData.name,
          mode: formData.mode,
          source_tag_key: formData.mode === "extract" ? formData.source_tag_key : null,
        })
      ).then(() => {
        if (!isError(UPDATE_VIRTUAL_TAG, getState())) {
          redirect();
        }
      });
    });
  };

  if (!virtualTag.id) {
    return null;
  }

  return (
    <VirtualTagForm
      defaultValues={{
        key: virtualTag.key || "",
        name: virtualTag.name || "",
        mode: virtualTag.mode || "assignment",
        source_tag_key: virtualTag.source_tag_key || "",
        quarter: virtualTag.quarter || "",
      }}
      isEdit
      onSubmit={onSubmit}
      onCancel={redirect}
      isLoading={isSaving}
      quarters={quarterOptions}
    />
  );
};

export default EditVirtualTagFormContainer;
