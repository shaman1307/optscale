export const CREATE_ORGANIZATION_CONSTRAINT_FORM_FIELD_NAMES = Object.freeze({
  EVALUATION_PERIOD: "evaluationPeriod",
  FILTERS: "filters",
  NAME: "name",
  THRESHOLD: "threshold",
  TYPE: "type",
  MAX_VALUE: "maxValue",
  MONTHLY_BUDGET: "monthlyBudget",
  TOTAL_BUDGET: "totalBudget",
  START_DATE: "startDate",
  TAGS_BAR: "tagsBar",
  PROHIBITED_TAG: "prohibitedTagField",
  REQUIRED_TAG: "requiredTagField",
  CORRELATION_TAG_1: "tagsCorrelationPrimaryTag",
  CORRELATION_TAG_2: "tagsCorrelationCorrelatedTag",
});

// Shared GET_AVAILABLE_FILTERS hash for this form. Filters + TagsInputs must
// use the same facets string or they thrash the single Redux cache and leave
// Save stuck in isLoading.
export const CREATE_ORGANIZATION_CONSTRAINT_AVAILABLE_FILTERS_FACETS = "core,tag,meta,virtual_tag";
