import { useEffect, useMemo } from "react";
import { useDispatch } from "react-redux";
import { useNavigate, useParams } from "react-router-dom";
import { getVirtualTag, getVirtualTagRules, updateVirtualTagRule } from "api";
import { GET_VIRTUAL_TAG, GET_VIRTUAL_TAG_RULES, UPDATE_VIRTUAL_TAG_RULE } from "api/restapi/actionTypes";
import VirtualTagRuleForm from "components/forms/VirtualTagRuleForm";
import { FIELD_NAMES } from "components/forms/AssignmentRuleForm/utils";
import { useAllDataSources } from "hooks/coreData/useAllDataSources";
import { useApiData } from "hooks/useApiData";
import { useApiState } from "hooks/useApiState";
import { useAssignmentRulesAvailableFilters } from "hooks/useAssignmentRulesAvailableFilters";
import { useOrganizationInfo } from "hooks/useOrganizationInfo";
import { getVirtualTagUrl } from "urls";
import { isError } from "utils/api";
import { TAG_IS, CLOUD_IS, TAG_VALUE_STARTS_WITH, RESOURCE_TYPE_IS, REGION_IS, DEFAULT_CONDITIONS } from "utils/constants";

const getConditions = (conditions = []) =>
  conditions.map((condition) => {
    const {
      ID,
      TYPE,
      META_INFO,
      TAG_KEY_FIELD_NAME,
      TAG_VALUE_FIELD_NAME,
      CLOUD_IS_FIELD_NAME,
      RESOURCE_TYPE_IS_FIELD_NAME,
      REGION_IS_FIELD_NAME,
    } = FIELD_NAMES.CONDITIONS_FIELD_ARRAY;
    const withId = condition.id ? { [ID]: condition.id } : {};
    if ([TAG_IS, TAG_VALUE_STARTS_WITH].includes(condition[TYPE])) {
      const { key, value } = JSON.parse(condition[META_INFO]);
      return { ...withId, [TYPE]: condition[TYPE], [TAG_KEY_FIELD_NAME]: key, [TAG_VALUE_FIELD_NAME]: value };
    }
    if (condition[TYPE] === CLOUD_IS) {
      return { ...withId, [TYPE]: condition[TYPE], [CLOUD_IS_FIELD_NAME]: condition[META_INFO] };
    }
    if (condition[TYPE] === RESOURCE_TYPE_IS) {
      return { ...withId, [TYPE]: condition[TYPE], [RESOURCE_TYPE_IS_FIELD_NAME]: condition[META_INFO] };
    }
    if (condition[TYPE] === REGION_IS) {
      return { ...withId, [TYPE]: condition[TYPE], [REGION_IS_FIELD_NAME]: { regionName: condition[META_INFO] } };
    }
    return { ...withId, [TYPE]: condition[TYPE], [META_INFO]: condition[META_INFO] };
  });

const EditVirtualTagRuleFormContainer = () => {
  const { virtualTagId, virtualTagRuleId } = useParams();
  const dispatch = useDispatch();
  const navigate = useNavigate();
  const { organizationId } = useOrganizationInfo();
  const dataSources = useAllDataSources();
  const { resourceTypes, regions } = useAssignmentRulesAvailableFilters();

  const {
    apiData: { virtualTag = {} },
  } = useApiData(GET_VIRTUAL_TAG, { virtualTag: {} });
  const {
    apiData: { virtualTagRules = [] },
  } = useApiData(GET_VIRTUAL_TAG_RULES, { virtualTagRules: [] });

  useEffect(() => {
    dispatch(getVirtualTag(virtualTagId));
    dispatch(getVirtualTagRules(organizationId, virtualTagId));
  }, [dispatch, organizationId, virtualTagId]);

  const rule = virtualTagRules.find((item) => item.id === virtualTagRuleId);
  const { isLoading } = useApiState(UPDATE_VIRTUAL_TAG_RULE);
  const redirect = () => navigate(getVirtualTagUrl(virtualTagId));

  const defaultValues = useMemo(
    () => ({
      name: rule?.name || "",
      active: rule?.active ?? true,
      branches: (rule?.branches || []).map((branch) => ({
        conditions: getConditions(branch.conditions).length ? getConditions(branch.conditions) : DEFAULT_CONDITIONS,
        allocations: (branch.allocations || []).map((item) => ({ value: item.value, share: item.share })),
      })),
    }),
    [rule]
  );

  const onSubmit = (params) => {
    dispatch((_, getState) => {
      dispatch(updateVirtualTagRule(virtualTagRuleId, params)).then(() => {
        if (!isError(UPDATE_VIRTUAL_TAG_RULE, getState())) {
          redirect();
        }
      });
    });
  };

  if (!rule) {
    return null;
  }

  return (
    <VirtualTagRuleForm
      virtualTag={{ ...virtualTag, id: virtualTagId }}
      defaultValues={defaultValues}
      isEdit
      cloudAccounts={dataSources}
      resourceTypes={resourceTypes}
      regions={regions}
      onSubmit={onSubmit}
      onCancel={redirect}
      isLoading={isLoading}
    />
  );
};

export default EditVirtualTagRuleFormContainer;
