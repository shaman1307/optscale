import { useEffect } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useDispatch } from "react-redux";
import { createVirtualTagRule, getVirtualTag } from "api";
import { CREATE_VIRTUAL_TAG_RULE, GET_VIRTUAL_TAG } from "api/restapi/actionTypes";
import VirtualTagRuleForm from "components/forms/VirtualTagRuleForm";
import { useAllDataSources } from "hooks/coreData/useAllDataSources";
import { useApiData } from "hooks/useApiData";
import { useApiState } from "hooks/useApiState";
import { useAssignmentRulesAvailableFilters } from "hooks/useAssignmentRulesAvailableFilters";
import { useOrganizationInfo } from "hooks/useOrganizationInfo";
import { getVirtualTagUrl } from "urls";
import { isError } from "utils/api";
import { DEFAULT_CONDITIONS } from "utils/constants";

const CreateVirtualTagRuleFormContainer = () => {
  const { virtualTagId } = useParams();
  const dispatch = useDispatch();
  const navigate = useNavigate();
  const { organizationId } = useOrganizationInfo();
  const dataSources = useAllDataSources();
  const { resourceTypes, regions } = useAssignmentRulesAvailableFilters();
  const {
    apiData: { virtualTag = {} },
  } = useApiData(GET_VIRTUAL_TAG, { virtualTag: { id: virtualTagId } });
  const { isLoading } = useApiState(CREATE_VIRTUAL_TAG_RULE);

  useEffect(() => {
    dispatch(getVirtualTag(virtualTagId));
  }, [dispatch, virtualTagId]);

  const redirect = () => navigate(getVirtualTagUrl(virtualTagId));

  const onSubmit = (params) => {
    dispatch((_, getState) => {
      dispatch(createVirtualTagRule(organizationId, params)).then(() => {
        if (!isError(CREATE_VIRTUAL_TAG_RULE, getState())) {
          redirect();
        }
      });
    });
  };

  return (
    <VirtualTagRuleForm
      virtualTag={{ ...virtualTag, id: virtualTagId }}
      defaultValues={{
        name: "",
        active: true,
        branches: [{ conditions: DEFAULT_CONDITIONS, allocations: [{ value: "", share: 100 }] }],
      }}
      cloudAccounts={dataSources}
      resourceTypes={resourceTypes}
      regions={regions}
      onSubmit={onSubmit}
      onCancel={redirect}
      isLoading={isLoading}
    />
  );
};

export default CreateVirtualTagRuleFormContainer;
