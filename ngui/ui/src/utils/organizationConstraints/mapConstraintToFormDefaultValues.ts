import { TYPE_CORRELATION, TYPE_PROHIBITED, TYPE_REQUIRED } from "components/CreateOrganizationConstraintForm/FormElements";
import { CREATE_ORGANIZATION_CONSTRAINT_FORM_FIELD_NAMES } from "components/CreateOrganizationConstraintForm/constants";
import { FILTER_CONFIGS } from "components/Resources/filterConfigs";
import { mapFiltersToApiParams } from "services/AvailableFiltersService";
import {
  CLOUD_ACCOUNT_ID_FILTER,
  EMPTY_UUID,
  OWNER_ID_FILTER,
  POOL_ID_FILTER,
  REGION_FILTER,
  RESOURCE_TYPE_FILTER,
  SERVICE_NAME_FILTER,
  ACCOUNT_LOCATOR_FILTER,
  K8S_NAMESPACE_FILTER,
  K8S_NODE_FILTER,
  K8S_SERVICE_FILTER,
  NETWORK_TRAFFIC_FROM_FILTER,
  NETWORK_TRAFFIC_TO_FILTER,
  ACTIVE_FILTER,
  RECOMMENDATIONS_FILTER,
  CONSTRAINT_VIOLATED_FILTER,
} from "utils/constants";
import { secondsToMilliseconds } from "utils/datetime";

const SELECTION_API_TO_FORM = {
  cloud_account_id: CLOUD_ACCOUNT_ID_FILTER,
  pool_id: POOL_ID_FILTER,
  owner_id: OWNER_ID_FILTER,
  region: REGION_FILTER,
  service_name: SERVICE_NAME_FILTER,
  resource_type: RESOURCE_TYPE_FILTER,
  account_locator: ACCOUNT_LOCATOR_FILTER,
  k8s_namespace: K8S_NAMESPACE_FILTER,
  k8s_node: K8S_NODE_FILTER,
  k8s_service: K8S_SERVICE_FILTER,
  traffic_from: NETWORK_TRAFFIC_FROM_FILTER,
  traffic_to: NETWORK_TRAFFIC_TO_FILTER,
};

const BOOL_API_TO_FORM = {
  active: ACTIVE_FILTER,
  recommendations: RECOMMENDATIONS_FILTER,
  constraint_violated: CONSTRAINT_VIOLATED_FILTER,
};

const getTaggingFormFields = (conditions = {}) => {
  const { tag, without_tag: withoutTag } = conditions;

  if (tag && withoutTag) {
    return {
      [CREATE_ORGANIZATION_CONSTRAINT_FORM_FIELD_NAMES.TAGS_BAR]: TYPE_CORRELATION,
      [CREATE_ORGANIZATION_CONSTRAINT_FORM_FIELD_NAMES.CORRELATION_TAG_1]: tag,
      [CREATE_ORGANIZATION_CONSTRAINT_FORM_FIELD_NAMES.CORRELATION_TAG_2]: withoutTag,
      [CREATE_ORGANIZATION_CONSTRAINT_FORM_FIELD_NAMES.REQUIRED_TAG]: "",
      [CREATE_ORGANIZATION_CONSTRAINT_FORM_FIELD_NAMES.PROHIBITED_TAG]: "",
    };
  }

  if (tag === EMPTY_UUID) {
    return {
      [CREATE_ORGANIZATION_CONSTRAINT_FORM_FIELD_NAMES.TAGS_BAR]: TYPE_REQUIRED,
      [CREATE_ORGANIZATION_CONSTRAINT_FORM_FIELD_NAMES.REQUIRED_TAG]: "",
      [CREATE_ORGANIZATION_CONSTRAINT_FORM_FIELD_NAMES.PROHIBITED_TAG]: "",
      [CREATE_ORGANIZATION_CONSTRAINT_FORM_FIELD_NAMES.CORRELATION_TAG_1]: "",
      [CREATE_ORGANIZATION_CONSTRAINT_FORM_FIELD_NAMES.CORRELATION_TAG_2]: "",
    };
  }

  if (withoutTag) {
    return {
      [CREATE_ORGANIZATION_CONSTRAINT_FORM_FIELD_NAMES.TAGS_BAR]: TYPE_REQUIRED,
      [CREATE_ORGANIZATION_CONSTRAINT_FORM_FIELD_NAMES.REQUIRED_TAG]: withoutTag,
      [CREATE_ORGANIZATION_CONSTRAINT_FORM_FIELD_NAMES.PROHIBITED_TAG]: "",
      [CREATE_ORGANIZATION_CONSTRAINT_FORM_FIELD_NAMES.CORRELATION_TAG_1]: "",
      [CREATE_ORGANIZATION_CONSTRAINT_FORM_FIELD_NAMES.CORRELATION_TAG_2]: "",
    };
  }

  if (tag) {
    return {
      [CREATE_ORGANIZATION_CONSTRAINT_FORM_FIELD_NAMES.TAGS_BAR]: TYPE_PROHIBITED,
      [CREATE_ORGANIZATION_CONSTRAINT_FORM_FIELD_NAMES.PROHIBITED_TAG]: tag,
      [CREATE_ORGANIZATION_CONSTRAINT_FORM_FIELD_NAMES.REQUIRED_TAG]: "",
      [CREATE_ORGANIZATION_CONSTRAINT_FORM_FIELD_NAMES.CORRELATION_TAG_1]: "",
      [CREATE_ORGANIZATION_CONSTRAINT_FORM_FIELD_NAMES.CORRELATION_TAG_2]: "",
    };
  }

  return {
    [CREATE_ORGANIZATION_CONSTRAINT_FORM_FIELD_NAMES.TAGS_BAR]: TYPE_REQUIRED,
    [CREATE_ORGANIZATION_CONSTRAINT_FORM_FIELD_NAMES.REQUIRED_TAG]: "",
    [CREATE_ORGANIZATION_CONSTRAINT_FORM_FIELD_NAMES.PROHIBITED_TAG]: "",
    [CREATE_ORGANIZATION_CONSTRAINT_FORM_FIELD_NAMES.CORRELATION_TAG_1]: "",
    [CREATE_ORGANIZATION_CONSTRAINT_FORM_FIELD_NAMES.CORRELATION_TAG_2]: "",
  };
};

const mapFiltersToFormValues = (filters = {}) => {
  const apiParams = mapFiltersToApiParams(filters);
  const formFilters = Object.fromEntries(
    Object.values(FILTER_CONFIGS).map((filterConfig) => [filterConfig.id, filterConfig.getDefaultValue()])
  );

  Object.entries(SELECTION_API_TO_FORM).forEach(([apiKey, formKey]) => {
    const values = apiParams[apiKey];
    if (Array.isArray(values) && values.length > 0) {
      formFilters[formKey] = { values };
    }
  });

  Object.entries(BOOL_API_TO_FORM).forEach(([apiKey, formKey]) => {
    const values = apiParams[apiKey];
    if (Array.isArray(values) && values.length > 0) {
      formFilters[formKey] = { values };
    }
  });

  if (apiParams.first_seen_gte || apiParams.first_seen_lte || filters.first_seen_gte || filters.first_seen_lte) {
    const from = apiParams.first_seen_gte ?? filters.first_seen_gte;
    const to = apiParams.first_seen_lte ?? filters.first_seen_lte;
    formFilters.firstSeen = {
      from: from ? secondsToMilliseconds(from) : undefined,
      to: to ? secondsToMilliseconds(to) : undefined,
    };
  }

  if (apiParams.last_seen_gte || apiParams.last_seen_lte || filters.last_seen_gte || filters.last_seen_lte) {
    const from = apiParams.last_seen_gte ?? filters.last_seen_gte;
    const to = apiParams.last_seen_lte ?? filters.last_seen_lte;
    formFilters.lastSeen = {
      from: from ? secondsToMilliseconds(from) : undefined,
      to: to ? secondsToMilliseconds(to) : undefined,
    };
  }

  return formFilters;
};

export const mapConstraintToFormDefaultValues = (constraint) => {
  const { name = "", type = "", definition = {}, filters = {} } = constraint;
  const {
    threshold_days: thresholdDays,
    threshold,
    max_value: maxValue,
    monthly_budget: monthlyBudget,
    total_budget: totalBudget,
    start_date: startDate,
    conditions,
  } = definition;

  return {
    [CREATE_ORGANIZATION_CONSTRAINT_FORM_FIELD_NAMES.NAME]: name,
    [CREATE_ORGANIZATION_CONSTRAINT_FORM_FIELD_NAMES.TYPE]: type,
    [CREATE_ORGANIZATION_CONSTRAINT_FORM_FIELD_NAMES.EVALUATION_PERIOD]: thresholdDays ?? "",
    [CREATE_ORGANIZATION_CONSTRAINT_FORM_FIELD_NAMES.THRESHOLD]: threshold ?? "",
    [CREATE_ORGANIZATION_CONSTRAINT_FORM_FIELD_NAMES.MAX_VALUE]: maxValue ?? "",
    [CREATE_ORGANIZATION_CONSTRAINT_FORM_FIELD_NAMES.MONTHLY_BUDGET]: monthlyBudget ?? "",
    [CREATE_ORGANIZATION_CONSTRAINT_FORM_FIELD_NAMES.TOTAL_BUDGET]: totalBudget ?? "",
    [CREATE_ORGANIZATION_CONSTRAINT_FORM_FIELD_NAMES.START_DATE]: startDate ? secondsToMilliseconds(startDate) : +new Date(),
    [CREATE_ORGANIZATION_CONSTRAINT_FORM_FIELD_NAMES.FILTERS]: mapFiltersToFormValues(filters),
    ...getTaggingFormFields(conditions),
  };
};
