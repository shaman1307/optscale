import AddOutlinedIcon from "@mui/icons-material/AddOutlined";
import DeleteOutlinedIcon from "@mui/icons-material/DeleteOutlined";
import { Autocomplete, Box, Paper, Typography } from "@mui/material";
import Link from "@mui/material/Link";
import { Controller, FormProvider, useFieldArray, useForm, useFormContext, useWatch } from "react-hook-form";
import { FormattedMessage } from "react-intl";
import { Link as RouterLink } from "react-router-dom";
import ActionBar from "components/ActionBar";
import Button from "components/Button";
import ButtonLoader from "components/ButtonLoader";
import FormButtonsWrapper from "components/FormButtonsWrapper";
import IconButton from "components/IconButton";
import InlineSeverityAlert from "components/InlineSeverityAlert";
import Input from "components/Input";
import PageContentWrapper from "components/PageContentWrapper";
import { NumberInput } from "components/forms/common/fields";
import { ActiveCheckboxField, ConditionsFieldArray, NameField } from "components/forms/AssignmentRuleForm/FormElements";
import { FIELD_NAMES as ASSIGNMENT_FIELD_NAMES } from "components/forms/AssignmentRuleForm/utils";
import { intl } from "translations/react-intl-config";
import { VIRTUAL_TAGS, getVirtualTagUrl } from "urls";
import { DEFAULT_CONDITIONS } from "utils/constants";
import { SPACING_1, SPACING_2 } from "utils/layouts";
import { idx } from "utils/objects";

const SHARE_FIELD_WIDTH = 104;
const ACTION_COLUMN_WIDTH = 40;

const sectionSx = {
  p: 1.5,
  borderRadius: 1,
  bgcolor: "action.hover",
};

const mapConditions = (conditions) => {
  const { FIELD_NAME, META_INFO, TYPE, TAG_KEY_FIELD_NAME, TAG_VALUE_FIELD_NAME, CLOUD_IS_FIELD_NAME, RESOURCE_TYPE_IS_FIELD_NAME, REGION_IS_FIELD_NAME, ID } =
    ASSIGNMENT_FIELD_NAMES.CONDITIONS_FIELD_ARRAY;

  return conditions.map((item) => {
    const withId = item[ID] ? { id: item[ID] } : {};
    if (TAG_KEY_FIELD_NAME in item) {
      return {
        ...withId,
        [META_INFO]: JSON.stringify({
          key: item[TAG_KEY_FIELD_NAME].trim(),
          value: item[TAG_VALUE_FIELD_NAME].trim(),
        }),
        [TYPE]: item[TYPE],
      };
    }
    if (CLOUD_IS_FIELD_NAME in item) {
      return { ...withId, [META_INFO]: item[CLOUD_IS_FIELD_NAME].trim(), [TYPE]: item[TYPE] };
    }
    if (RESOURCE_TYPE_IS_FIELD_NAME in item) {
      return { ...withId, [META_INFO]: item[RESOURCE_TYPE_IS_FIELD_NAME].trim(), [TYPE]: item[TYPE] };
    }
    if (REGION_IS_FIELD_NAME in item) {
      const { regionName } = item[REGION_IS_FIELD_NAME] || {};
      return { ...withId, [META_INFO]: regionName === null ? null : regionName?.trim(), [TYPE]: item[TYPE] };
    }
    return { ...withId, meta_info: (item[META_INFO] || "").trim(), type: item[TYPE] };
  });
};

const AllocationValueField = ({ name, options }) => {
  const {
    control,
    formState: { errors },
  } = useFormContext();
  const fieldError = idx(name.split("."), errors);

  return (
    <Controller
      name={name}
      control={control}
      rules={{
        required: {
          value: true,
          message: intl.formatMessage({ id: "thisFieldIsRequired" }),
        },
      }}
      render={({ field: { value, onChange, ...rest } }) => (
        <Autocomplete
          fullWidth
          freeSolo
          options={options}
          value={value || ""}
          onChange={(event, newValue) => onChange(newValue ?? "")}
          onInputChange={(event, newInputValue, reason) => {
            if (reason === "input" || reason === "clear") {
              onChange(newInputValue);
            }
          }}
          sx={{ minWidth: 0 }}
          renderInput={(autoCompleteParams) => (
            <Input
              required
              label={<FormattedMessage id="value" />}
              error={!!fieldError}
              helperText={fieldError?.message}
              {...rest}
              {...autoCompleteParams}
            />
          )}
        />
      )}
    />
  );
};

const AllocationsFieldArray = ({ name, allocationValues = [] }) => {
  const { fields, append, remove } = useFieldArray({ name });
  return (
    <Box>
      {fields.map((field, index) => (
        <Box
          key={field.id}
          display="grid"
          gap={SPACING_1}
          alignItems="flex-start"
          sx={{
            mb: 1,
            gridTemplateColumns: {
              xs: "minmax(0, 1fr) auto",
              sm: `minmax(0, 1fr) ${SHARE_FIELD_WIDTH}px ${ACTION_COLUMN_WIDTH}px`,
            },
          }}
        >
          <Box sx={{ minWidth: 0, gridColumn: { xs: "1 / -1", sm: "auto" } }}>
            <AllocationValueField name={`${name}.${index}.value`} options={allocationValues} />
          </Box>
          <Box sx={{ width: SHARE_FIELD_WIDTH }}>
            <NumberInput
              name={`${name}.${index}.share`}
              label={<FormattedMessage id="sharePercent" />}
              required
              min={1}
              max={100}
              valueAsNumber
            />
          </Box>
          <Box
            sx={{
              display: "flex",
              alignItems: "flex-end",
              pb: 1,
            }}
          >
            <IconButton
              color="error"
              icon={<DeleteOutlinedIcon />}
              onClick={() => fields.length > 1 && remove(index)}
              disabled={fields.length <= 1}
              tooltip={{
                show: true,
                value: <FormattedMessage id="delete" />,
              }}
              dataTestId={`btn_delete_allocation_${index}`}
            />
          </Box>
        </Box>
      ))}
      <Button
        dashedBorder
        startIcon={<AddOutlinedIcon />}
        messageId="addAllocation"
        size="small"
        onClick={() => append({ value: "", share: 0 })}
      />
    </Box>
  );
};

const VirtualTagRuleForm = ({
  virtualTag,
  defaultValues,
  onSubmit,
  onCancel,
  isEdit = false,
  isLoading = false,
  cloudAccounts = [],
  resourceTypes = [],
  regions = [],
}) => {
  const methods = useForm({ defaultValues });
  const { handleSubmit, control } = methods;
  const { fields, append, remove } = useFieldArray({ control, name: "branches" });
  const branches = useWatch({ control, name: "branches" }) || [];

  const branchShareSums = branches.map((branch) =>
    (branch.allocations || []).reduce((sum, item) => sum + Number(item.share || 0), 0)
  );
  const isSplit = branches.some(
    (branch) => (branch.allocations || []).length > 1 || (branch.allocations || []).some((item) => Number(item.share) !== 100)
  );
  const sharesAreValid =
    virtualTag.mode === "extract" ||
    (branchShareSums.length > 0 && branchShareSums.every((sum) => sum === 100));
  const splitOrInvalid = isSplit && fields.length > 1;

  const submit = (formData) => {
    onSubmit({
      name: formData.name,
      active: formData.active,
      virtual_tag_id: virtualTag.id,
      branches: formData.branches.map((branch) => ({
        conditions: mapConditions(branch.conditions),
        allocations:
          virtualTag.mode === "extract"
            ? []
            : (branch.allocations || []).map((item) => ({
                value: item.value.trim(),
                share: Number(item.share),
              })),
      })),
    });
  };

  return (
    <>
      <ActionBar
        data={{
          breadcrumbs: [
            <Link key={1} to={VIRTUAL_TAGS} component={RouterLink}>
              <FormattedMessage id="virtualTags" />
            </Link>,
            <Link key={2} to={getVirtualTagUrl(virtualTag.id)} component={RouterLink}>
              {virtualTag.name || virtualTag.key}
            </Link>,
          ],
          title: {
            text: <FormattedMessage id={isEdit ? "editVirtualTagRule" : "addVirtualTagRule"} />,
          },
        }}
      />
      <PageContentWrapper>
        <Box sx={{ width: "100%", maxWidth: 880 }}>
          <FormProvider {...methods}>
            <form onSubmit={handleSubmit(submit)} noValidate>
              <Box
                display="flex"
                flexWrap="wrap"
                alignItems="center"
                gap={SPACING_2}
                sx={{ mb: 1 }}
              >
                <Box sx={{ flex: "1 1 280px", minWidth: 0 }}>
                  <NameField />
                </Box>
                <Box sx={{ pt: 1 }}>
                  <ActiveCheckboxField />
                </Box>
              </Box>
              <InlineSeverityAlert messageId="virtualTagCloudIsHint" severity="info" sx={{ mb: 1.5 }} />
              {fields.map((field, index) => (
                <Box key={field.id}>
                  {index > 0 && (
                    <Typography
                      variant="overline"
                      color="text.secondary"
                      sx={{ display: "block", textAlign: "center", my: 1 }}
                    >
                      <FormattedMessage id="or" />
                    </Typography>
                  )}
                  <Paper variant="outlined" sx={{ p: 1.5, mb: 0 }}>
                    <Box display="flex" alignItems="center" justifyContent="space-between" sx={{ mb: 1.5, minHeight: 32 }}>
                      <Typography variant="subtitle2">
                        <FormattedMessage id="conditionGroup" values={{ number: index + 1 }} />
                      </Typography>
                      {fields.length > 1 && (
                        <IconButton
                          color="error"
                          icon={<DeleteOutlinedIcon />}
                          onClick={() => remove(index)}
                          tooltip={{
                            show: true,
                            value: <FormattedMessage id="deleteConditionGroup" />,
                          }}
                          dataTestId={`btn_delete_branch_${index}`}
                        />
                      )}
                    </Box>
                    <Box sx={{ ...sectionSx, mb: virtualTag.mode === "extract" ? 0 : 1.5 }}>
                      <Typography variant="subtitle2" sx={{ mb: 1 }}>
                        <FormattedMessage id="conditions" />
                      </Typography>
                      <ConditionsFieldArray
                        name={`branches.${index}.conditions`}
                        cloudAccounts={cloudAccounts}
                        resourceTypes={resourceTypes}
                        regions={regions}
                        enableResourceNameAutocomplete
                        enableDataSourceFilterPicker
                        compact
                        addMessageId="addCondition"
                      />
                    </Box>
                    {virtualTag.mode !== "extract" && (
                      <Box sx={sectionSx}>
                        <Typography variant="subtitle2" sx={{ mb: 1 }}>
                          <FormattedMessage id="allocations" />
                        </Typography>
                        <AllocationsFieldArray
                          name={`branches.${index}.allocations`}
                          allocationValues={virtualTag.allocation_values || []}
                        />
                        {branchShareSums[index] !== 100 && (
                          <InlineSeverityAlert messageId="virtualTagSharesMustSum100" severity="warning" sx={{ mt: 1 }} />
                        )}
                      </Box>
                    )}
                  </Paper>
                </Box>
              ))}
              {splitOrInvalid && <InlineSeverityAlert messageId="virtualTagSplitOrDisabled" severity="warning" sx={{ mt: 1 }} />}
              <Box sx={{ mt: 1.5 }}>
                <Button
                  dashedBorder
                  startIcon={<AddOutlinedIcon />}
                  messageId="addConditionGroup"
                  disabled={isSplit}
                  onClick={() =>
                    append({
                      conditions: DEFAULT_CONDITIONS,
                      allocations: [{ value: "", share: 100 }],
                    })
                  }
                />
              </Box>
              <FormButtonsWrapper>
                <ButtonLoader
                  messageId={isEdit ? "save" : "create"}
                  color="primary"
                  variant="contained"
                  type="submit"
                  isLoading={isLoading}
                  disabled={!sharesAreValid || splitOrInvalid}
                />
                <Button messageId="cancel" onClick={onCancel} />
              </FormButtonsWrapper>
            </form>
          </FormProvider>
        </Box>
      </PageContentWrapper>
    </>
  );
};

export default VirtualTagRuleForm;
