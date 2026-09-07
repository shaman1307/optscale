import { Children, Fragment, useEffect, useMemo, useState } from "react";
import AddOutlinedIcon from "@mui/icons-material/AddOutlined";
import DeleteOutlinedIcon from "@mui/icons-material/DeleteOutlined";
import { Autocomplete, Typography } from "@mui/material";
import Box from "@mui/material/Box";
import FormControl from "@mui/material/FormControl";
import { Controller, useFieldArray, useFormContext } from "react-hook-form";
import { FormattedMessage } from "react-intl";
import { makeStyles } from "tss-react/mui";
import { getApiUrl } from "api/utils";
import Button from "components/Button";
import CloudTypeIcon from "components/CloudTypeIcon";
import { groupItemsByKey } from "components/FilterComponents/selectionFilterGroups";
import { FILTER_CONFIGS } from "components/Resources/filterConfigs";
import { Selector, TextInput } from "components/forms/common/fields";
import IconButton from "components/IconButton";
import IconLabel from "components/IconLabel";
import Input from "components/Input";
import InputLoader from "components/InputLoader";
import ResourceTypeLabel from "components/ResourceTypeLabel";
import { ItemContent, ItemContentWithDataSourceIcon } from "components/Selector";
import { useDebouncedValue } from "hooks/useDebouncedValue";
import { useGetToken } from "hooks/useGetToken";
import { useOrganizationInfo } from "hooks/useOrganizationInfo";
import { intl } from "translations/react-intl-config";
import {
  CONDITION_TYPES,
  TAG_IS,
  CLOUD_IS,
  TAG_VALUE_STARTS_WITH,
  TAG_EXISTS,
  DEFAULT_CONDITION,
  ARRAY_FORM_FIELD_FLEX_BASIS_WIDTH,
  RESOURCE_TYPE_IS,
  REGION_IS,
  NAME_ID_IS,
  OPTSCALE_RESOURCE_TYPES,
} from "utils/constants";
import { SPACING_1 } from "utils/layouts";
import { idx } from "utils/objects";
import { notOnlyWhiteSpaces } from "utils/validation";
import { ConditionsFieldArrayProps } from "../types";
import { FIELD_NAMES } from "../utils";

const { FIELD_NAME, TYPE, META_INFO } = FIELD_NAMES.CONDITIONS_FIELD_ARRAY;

const useStyles = makeStyles()((theme) => ({
  item: {
    width: "100%",
    minWidth: 0,
  },
  keyValueInput: {
    width: `calc(50% - ${theme.spacing(SPACING_1 / 2)})`,
  },
  spaceRight: {
    marginRight: theme.spacing(1),
  },
  deleteButton: {
    display: "flex",
    alignItems: "flex-end",
    paddingBottom: theme.spacing(1),
  },
}));

export const NOT_SET_REGION_FILTER_NAME = intl.formatMessage({ id: "notSet" });

const ResourceTypeIsAutocompleteField = ({ resourceTypes, name, field, count }) => {
  const {
    control,
    formState: { errors },
  } = useFormContext();

  const NAME = FIELD_NAMES.CONDITIONS_FIELD_ARRAY.RESOURCE_TYPE_IS_FIELD_NAME;

  const fieldName = `${name}.${count}.${NAME}`;
  const fieldError = idx(fieldName.split("."), errors);

  return (
    <Controller
      name={`${name}.${count}.${NAME}`}
      control={control}
      defaultValue={field[NAME] ?? ""}
      rules={{
        required: {
          value: true,
          message: intl.formatMessage({ id: "thisFieldIsRequired" }),
        },
      }}
      render={({ field: { value: formFieldValue, onChange, ...rest } }) => (
        <Autocomplete
          freeSolo
          options={resourceTypes.toSorted((resourceTypeA, resourceTypeB) => {
            if (resourceTypeA.type === resourceTypeB.type) {
              return resourceTypeA.name.localeCompare(resourceTypeB.name);
            }

            return resourceTypeA.type.localeCompare(resourceTypeB.type);
          })}
          value={formFieldValue}
          onChange={(event, newValue) => {
            onChange(newValue?.name ?? "");
          }}
          onInputChange={(event, newInputValue) => {
            onChange(newInputValue);
          }}
          isOptionEqualToValue={(option, value) => option.name === value}
          getOptionLabel={(option) => {
            if (typeof option === "string") {
              return option;
            }
            return option.name;
          }}
          renderOption={(props, option) => {
            const getOptionLabel = () => {
              if ([OPTSCALE_RESOURCE_TYPES.CLUSTER, OPTSCALE_RESOURCE_TYPES.ENVIRONMENT].includes(option.type)) {
                return (
                  <ResourceTypeLabel
                    resourceInfo={{
                      resourceType: option.name,
                      clusterTypeId: option.type === OPTSCALE_RESOURCE_TYPES.CLUSTER,
                      isEnvironment: option.type === OPTSCALE_RESOURCE_TYPES.ENVIRONMENT,
                    }}
                  />
                );
              }

              return option.name;
            };

            return <li {...props}>{getOptionLabel()}</li>;
          }}
          renderInput={(autoCompleteParams) => (
            <Input
              required
              label={<FormattedMessage id="resourceType" />}
              dataTestId={`input_${fieldName}`}
              error={!!fieldError}
              helperText={fieldError?.message}
              {...autoCompleteParams}
              {...rest}
            />
          )}
        />
      )}
    />
  );
};

const RegionIsAutocompleteField = ({ regions, name, count }) => {
  const {
    control,
    formState: { errors },
  } = useFormContext();

  const NAME = FIELD_NAMES.CONDITIONS_FIELD_ARRAY.REGION_IS_FIELD_NAME;

  const fieldName = `${name}.${count}.${NAME}`;
  const fieldError = idx(fieldName.split("."), errors);

  return (
    <Controller
      name={fieldName}
      control={control}
      defaultValue={null}
      rules={{
        validate: {
          required: (value) => {
            if (value === null) {
              return intl.formatMessage({ id: "thisFieldIsRequired" });
            }

            return true;
          },
          notOnlyWhiteSpaces: (value) => {
            if (value === null) {
              return true;
            }

            const { regionName } = value;

            if (regionName === null) {
              return true;
            }

            return notOnlyWhiteSpaces(regionName);
          },
        },
      }}
      render={({ field: { value: formFieldValue, onChange, ...rest } }) => (
        <Autocomplete
          freeSolo
          options={regions
            .map((region) => {
              if (region === null) {
                return {
                  regionName: null,
                  dataSourceType: undefined,
                };
              }
              return {
                regionName: region.name,
                dataSourceType: region.cloud_type,
              };
            })
            .toSorted((regionA, regionB) => {
              if (regionA.dataSourceType === undefined && regionB.dataSourceType !== undefined) {
                return -1; // Place regionA (with undefined dataSourceType) first
              }

              if (regionA.dataSourceType !== undefined && regionB.dataSourceType === undefined) {
                return 1; // Place regionB (with undefined dataSourceType) first
              }

              if (regionA.dataSourceType === regionB.dataSourceType) {
                return regionA.regionName.localeCompare(regionB.regionName);
              }

              return regionA.dataSourceType.localeCompare(regionB.dataSourceType);
            })}
          value={formFieldValue}
          onChange={(event, newValue) => {
            if (newValue === null) {
              onChange(null);
            } else {
              onChange({
                regionName: newValue.regionName,
              });
            }
          }}
          onInputChange={(event, newInputValue) => {
            if (newInputValue === NOT_SET_REGION_FILTER_NAME) {
              onChange({
                regionName: null,
              });
              return;
            }
            if (newInputValue === "") {
              onChange(null);
              return;
            }
            onChange({
              regionName: newInputValue,
            });
          }}
          isOptionEqualToValue={(option, autocompleteValue) => option.regionName === autocompleteValue.regionName}
          getOptionLabel={(option) => (option.regionName === null ? NOT_SET_REGION_FILTER_NAME : option.regionName)}
          renderOption={(props, option) => (
            <li {...props}>
              {option.regionName ? (
                <IconLabel icon={<CloudTypeIcon type={option.dataSourceType} hasRightMargin />} label={option.regionName} />
              ) : (
                NOT_SET_REGION_FILTER_NAME
              )}
            </li>
          )}
          renderInput={(autoCompleteParams) => (
            <Input
              required
              label={<FormattedMessage id="region" />}
              dataTestId={`input_${fieldName}`}
              error={!!fieldError}
              helperText={fieldError?.message}
              {...autoCompleteParams}
              {...rest}
            />
          )}
        />
      )}
    />
  );
};

const NameIdAutocompleteField = ({ name, count, field }) => {
  const {
    control,
    formState: { errors },
    watch,
  } = useFormContext();
  const { organizationId } = useOrganizationInfo();
  const { token } = useGetToken();
  const [options, setOptions] = useState([]);
  const [inputValue, setInputValue] = useState(field[META_INFO] ?? "");
  const debouncedSearch = useDebouncedValue(inputValue, { delay: 300 });
  const conditions = watch(name) || [];
  const cloudAccountIds = conditions
    .filter((item) => item?.[TYPE] === CLOUD_IS)
    .map((item) => item[FIELD_NAMES.CONDITIONS_FIELD_ARRAY.CLOUD_IS_FIELD_NAME])
    .filter(Boolean);
  const cloudKey = cloudAccountIds.join(",");
  const fieldName = `${name}.${count}.${META_INFO}`;
  const fieldError = idx(fieldName.split("."), errors);

  useEffect(() => {
    const search = String(debouncedSearch || "").trim();
    if (!organizationId || !token || search.length < 1) {
      setOptions([]);
      return undefined;
    }
    const params = new URLSearchParams();
    params.set("search", search);
    params.set("limit", "20");
    (cloudKey ? cloudKey.split(",") : []).forEach((id) => params.append("cloud_account_id", id));
    const controller = new AbortController();
    fetch(`${getApiUrl("restapi")}/organizations/${organizationId}/virtual_tag_resource_options?${params}`, {
      headers: { Authorization: `Bearer ${token}` },
      signal: controller.signal,
    })
      .then((response) => (response.ok ? response.json() : { resources: [] }))
      .then((data) => setOptions(data.resources || []))
      .catch((error) => {
        if (error.name !== "AbortError") {
          setOptions([]);
        }
      });
    return () => controller.abort();
  }, [cloudKey, debouncedSearch, organizationId, token]);

  return (
    <Controller
      name={fieldName}
      control={control}
      defaultValue={field[META_INFO] ?? ""}
      rules={{
        required: {
          value: true,
          message: intl.formatMessage({ id: "thisFieldIsRequired" }),
        },
      }}
      render={({ field: { value: formFieldValue, onChange, ...rest } }) => (
        <Autocomplete
          freeSolo
          options={options}
          filterOptions={(current) => current}
          value={formFieldValue || ""}
          inputValue={inputValue}
          onInputChange={(event, newInputValue) => {
            setInputValue(newInputValue);
            if (event) {
              onChange(newInputValue);
            }
          }}
          onChange={(event, newValue) => {
            if (newValue == null) {
              onChange("");
              setInputValue("");
              return;
            }
            if (typeof newValue === "string") {
              onChange(newValue);
              setInputValue(newValue);
              return;
            }
            const resourceId = newValue.cloud_resource_id || "";
            onChange(resourceId);
            setInputValue(resourceId);
          }}
          isOptionEqualToValue={(option, selected) =>
            option.cloud_resource_id === (typeof selected === "string" ? selected : selected?.cloud_resource_id)
          }
          getOptionLabel={(option) => {
            if (typeof option === "string") {
              return option;
            }
            return option.cloud_resource_id || option.name || "";
          }}
          renderOption={(props, option) => (
            <li {...props} key={option.cloud_resource_id || option.id}>
              <Box>
                <Box>{option.name || option.cloud_resource_id}</Box>
                <Box sx={{ color: "text.secondary", fontSize: "0.75rem" }}>{option.cloud_resource_id}</Box>
              </Box>
            </li>
          )}
          renderInput={(autoCompleteParams) => (
            <Input
              required
              label={<FormattedMessage id="nameIdIs" />}
              dataTestId={`input_${fieldName}`}
              error={!!fieldError}
              helperText={fieldError?.message}
              {...autoCompleteParams}
              {...rest}
            />
          )}
        />
      )}
    />
  );
};


const CLOUD_ACCOUNT_FILTER = FILTER_CONFIGS.cloudAccountId;

const DataSourceIsAutocompleteField = ({ cloudAccounts, name, count, field }) => {
  const {
    control,
    formState: { errors },
  } = useFormContext();

  const NAME = FIELD_NAMES.CONDITIONS_FIELD_ARRAY.CLOUD_IS_FIELD_NAME;
  const fieldName = `${name}.${count}.${NAME}`;
  const fieldError = idx(fieldName.split("."), errors);

  const options = useMemo(() => {
    const items = CLOUD_ACCOUNT_FILTER.transformers.getItems(cloudAccounts);
    return groupItemsByKey(items, CLOUD_ACCOUNT_FILTER.groupBy, CLOUD_ACCOUNT_FILTER.sortGroups).flatMap(
      (group) => group.items
    );
  }, [cloudAccounts]);

  return (
    <Controller
      name={fieldName}
      control={control}
      defaultValue={field[NAME] ?? ""}
      rules={{
        required: {
          value: true,
          message: intl.formatMessage({ id: "thisFieldIsRequired" }),
        },
      }}
      render={({ field: { value: formFieldValue, onChange, ...rest } }) => {
        const selected = options.find((option) => option.value === formFieldValue) ?? null;

        return (
          <Autocomplete
            options={options}
            value={selected}
            onChange={(event, newValue) => onChange(newValue?.value ?? "")}
            isOptionEqualToValue={(option, selectedValue) => option.value === selectedValue?.value}
            getOptionLabel={(option) => option.name || ""}
            groupBy={(option) => CLOUD_ACCOUNT_FILTER.groupBy(option)}
            filterOptions={(currentOptions, state) => {
              const query = state.inputValue;
              if (!query) {
                return currentOptions;
              }
              return currentOptions.filter((option) => CLOUD_ACCOUNT_FILTER.searchPredicate(option, query));
            }}
            renderGroup={(params) => (
              <li key={params.key}>
                <Box
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    gap: 0.75,
                    px: 1,
                    py: 0.5,
                    bgcolor: "action.hover",
                    position: "sticky",
                    top: 0,
                    zIndex: 1,
                  }}
                >
                  {CLOUD_ACCOUNT_FILTER.renderGroupHeader(params.group)}
                  <Typography variant="caption" color="text.secondary" component="span">
                    ({Children.count(params.children)})
                  </Typography>
                </Box>
                <Box component="ul" sx={{ m: 0, p: 0 }}>
                  {params.children}
                </Box>
              </li>
            )}
            renderOption={(props, option) => (
              <li {...props} key={option.value}>
                {CLOUD_ACCOUNT_FILTER.renderItem(option)}
              </li>
            )}
            renderInput={(autoCompleteParams) => (
              <Input
                required
                label={<FormattedMessage id="dataSource" />}
                dataTestId={`input_${fieldName}`}
                error={!!fieldError}
                helperText={fieldError?.message}
                {...autoCompleteParams}
                {...rest}
              />
            )}
          />
        );
      }}
    />
  );
};

const ConditionsFieldArray = ({
  name = FIELD_NAME,
  isLoading = false,
  cloudAccounts,
  resourceTypes,
  regions,
  enableResourceNameAutocomplete = false,
  enableDataSourceFilterPicker = false,
  compact = false,
  addMessageId = "add",
}: ConditionsFieldArrayProps) => {
  const { classes, cx } = useStyles();

  const { control, watch } = useFormContext();

  const { fields, append, remove } = useFieldArray({
    control,
    name,
  });

  const watchConditions = watch(name);

  const renderInputField = (field, count, labelMessageId = "value") => (
    <TextInput
      name={`${name}.${count}.${META_INFO}`}
      className={classes.item}
      dataTestId={`input_${labelMessageId}_${count}`}
      defaultValue={field[META_INFO]}
      fullWidth={false}
      label={<FormattedMessage id={labelMessageId} />}
      required
    />
  );

  const renderKeyValueField = (field, count) => {
    const KEY_INPUT_NAME = FIELD_NAMES.CONDITIONS_FIELD_ARRAY.TAG_KEY_FIELD_NAME;
    const VALUE_INPUT_NAME = FIELD_NAMES.CONDITIONS_FIELD_ARRAY.TAG_VALUE_FIELD_NAME;

    return (
      <Box className={classes.item}>
        <TextInput
          name={`${name}.${count}.${KEY_INPUT_NAME}`}
          defaultValue={field[KEY_INPUT_NAME]}
          fullWidth={false}
          label={<FormattedMessage id="key" />}
          dataTestId={`input_key_${count}`}
          required
          autoFocus={false}
          className={cx(classes.spaceRight, classes.keyValueInput)}
        />
        <TextInput
          name={`${name}.${count}.${VALUE_INPUT_NAME}`}
          defaultValue={field[VALUE_INPUT_NAME]}
          fullWidth={false}
          label={<FormattedMessage id="value" />}
          dataTestId={`input_value_${count}`}
          required
          autoFocus={false}
          className={classes.keyValueInput}
        />
      </Box>
    );
  };

  const renderCloudAccountSelector = (field, count) => {
    if (enableDataSourceFilterPicker) {
      return <DataSourceIsAutocompleteField cloudAccounts={cloudAccounts} name={name} count={count} field={field} />;
    }

    const NAME = FIELD_NAMES.CONDITIONS_FIELD_ARRAY.CLOUD_IS_FIELD_NAME;

    return (
      <Selector
        name={`${name}.${count}.${NAME}`}
        defaultValue={field[NAME] ?? ""}
        id={`selector-cloud-account-${count}`}
        fullWidth
        required
        labelMessageId="dataSource"
        items={cloudAccounts.map(({ id, name: cloudAccountName, type }) => ({
          value: id,
          content: <ItemContentWithDataSourceIcon dataSourceType={type}>{cloudAccountName}</ItemContentWithDataSourceIcon>,
        }))}
      />
    );
  };

  const conditionRow = (field, count) => {
    const condition = watchConditions?.[count]?.[TYPE];

    const renderField = () => {
      switch (condition) {
        case TAG_IS:
        case TAG_VALUE_STARTS_WITH:
          return renderKeyValueField(field, count);
        case CLOUD_IS:
          return renderCloudAccountSelector(field, count);
        case TAG_EXISTS:
          return renderInputField(field, count, "key");
        case RESOURCE_TYPE_IS:
          return <ResourceTypeIsAutocompleteField resourceTypes={resourceTypes} name={name} field={field} count={count} />;
        case REGION_IS:
          return <RegionIsAutocompleteField regions={regions} name={name} count={count} />;
        case NAME_ID_IS:
          if (enableResourceNameAutocomplete) {
            return <NameIdAutocompleteField name={name} count={count} field={field} />;
          }
          return renderInputField(field, count);
        default:
          return renderInputField(field, count);
      }
    };

    return (
      <Box display="flex" gap={SPACING_1} flexWrap="wrap" sx={{ mb: compact ? 1 : 0 }}>
        <Box flexBasis={ARRAY_FORM_FIELD_FLEX_BASIS_WIDTH.MEDIUM} flexGrow={1}>
          <Selector
            name={`${name}.${count}.${TYPE}`}
            defaultValue={field[TYPE]}
            id={`selector-type-${count}`}
            fullWidth
            required
            labelMessageId="type"
            items={Object.entries(CONDITION_TYPES).map(([conditionType, conditionMessageId]) => ({
              value: conditionType,
              content: (
                <ItemContent>
                  <FormattedMessage id={conditionMessageId} />
                </ItemContent>
              ),
            }))}
          />
        </Box>
        <Box display="flex" flexBasis={ARRAY_FORM_FIELD_FLEX_BASIS_WIDTH.MEDIUM} flexGrow={2} gap={SPACING_1}>
          <Box flexGrow={1} minWidth={0}>
            {renderField()}
          </Box>
          <Box className={classes.deleteButton}>
            <FormControl>
              <IconButton
                color="error"
                icon={<DeleteOutlinedIcon />}
                onClick={() => (fields.length > 1 ? remove(count) : null)}
                disabled={fields.length === 1}
                tooltip={{
                  show: true,
                  value: <FormattedMessage id="delete" />,
                }}
                dataTestId={`btn_delete_${count}`}
              />
            </FormControl>
          </Box>
        </Box>
      </Box>
    );
  };

  return isLoading ? (
    <InputLoader fullWidth />
  ) : (
    <>
      {fields.map((item, index) => (
        <Fragment key={item.id}>{conditionRow(item, index)}</Fragment>
      ))}
      {compact ? (
        <Box>
          <Button
            dashedBorder
            startIcon={<AddOutlinedIcon />}
            messageId={addMessageId}
            dataTestId="btn_add"
            size="small"
            color="primary"
            onClick={() => append(DEFAULT_CONDITION)}
          />
        </Box>
      ) : (
        <FormControl fullWidth>
          <Button
            dashedBorder
            startIcon={<AddOutlinedIcon />}
            messageId={addMessageId}
            dataTestId="btn_add"
            size="large"
            color="primary"
            onClick={() => append(DEFAULT_CONDITION)}
          />
        </FormControl>
      )}
    </>
  );
};

export default ConditionsFieldArray;
