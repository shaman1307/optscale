import { FormattedMessage } from "react-intl";
import { RadioGroup, TextInput } from "components/forms/common/fields";
import QuestionMark from "components/QuestionMark";

export const FIELD_NAMES = Object.freeze({
  ACCOUNT: "account",
  USER: "user",
  PRIVATE_KEY: "privateKey",
  ROLE: "role",
  WAREHOUSE: "warehouse",
  BACKUP_WAREHOUSE: "backupWarehouse",
  BILLING_SOURCE: "billingSource",
});

export const BILLING_SOURCE_VALUES = Object.freeze({
  ACCOUNT_USAGE: "account_usage",
  ORGANIZATION_USAGE: "organization_usage",
});

const SnowflakeCredentials = ({ readOnlyFields = [], privateKeyRequired = true }) => {
  const isReadOnly = (fieldName) => readOnlyFields.includes(fieldName);

  return (
    <>
      <TextInput
        name={FIELD_NAMES.ACCOUNT}
        required
        InputProps={{
          readOnly: isReadOnly(FIELD_NAMES.ACCOUNT),
          endAdornment: isReadOnly(FIELD_NAMES.ACCOUNT) ? null : (
            <QuestionMark messageId="snowflakeAccountTooltip" dataTestId="qmark_snowflake_account" />
          ),
        }}
        label={<FormattedMessage id="account" />}
        dataTestId="input_snowflake_account"
      />
      <TextInput
        name={FIELD_NAMES.USER}
        required
        InputProps={{
          endAdornment: <QuestionMark messageId="snowflakeUserTooltip" dataTestId="qmark_snowflake_user" />,
        }}
        label={<FormattedMessage id="user" />}
        dataTestId="input_snowflake_user"
      />
      <TextInput
        name={FIELD_NAMES.PRIVATE_KEY}
        required={privateKeyRequired}
        masked
        multiline
        minRows={4}
        maxLength={null}
        InputProps={{
          endAdornment: <QuestionMark messageId="snowflakePrivateKeyTooltip" dataTestId="qmark_snowflake_key" />,
        }}
        label={<FormattedMessage id="privateKey" />}
        autoComplete="off"
        dataTestId="input_snowflake_private_key"
      />
      <TextInput
        name={FIELD_NAMES.WAREHOUSE}
        required
        InputProps={{
          endAdornment: <QuestionMark messageId="snowflakeWarehouseTooltip" dataTestId="qmark_snowflake_wh" />,
        }}
        label={<FormattedMessage id="warehouse" />}
        dataTestId="input_snowflake_warehouse"
      />
      <TextInput
        name={FIELD_NAMES.BACKUP_WAREHOUSE}
        InputProps={{
          endAdornment: <QuestionMark messageId="snowflakeBackupWarehouseTooltip" dataTestId="qmark_snowflake_backup_wh" />,
        }}
        label={<FormattedMessage id="backupWarehouse" />}
        dataTestId="input_snowflake_backup_warehouse"
      />
      <TextInput
        name={FIELD_NAMES.ROLE}
        defaultValue="ACCOUNTADMIN"
        InputProps={{
          endAdornment: <QuestionMark messageId="snowflakeRoleTooltip" dataTestId="qmark_snowflake_role" />,
        }}
        label={<FormattedMessage id="role" />}
        dataTestId="input_snowflake_role"
      />
      <RadioGroup
        name={FIELD_NAMES.BILLING_SOURCE}
        defaultValue={BILLING_SOURCE_VALUES.ACCOUNT_USAGE}
        labelMessageId="snowflakeBillingSource"
        radioButtons={[
          {
            value: BILLING_SOURCE_VALUES.ACCOUNT_USAGE,
            label: <FormattedMessage id="snowflakeBillingSourceAccountUsage" />,
            dataTestId: "radio_snowflake_account_usage",
          },
          {
            value: BILLING_SOURCE_VALUES.ORGANIZATION_USAGE,
            label: <FormattedMessage id="snowflakeBillingSourceOrganizationUsage" />,
            dataTestId: "radio_snowflake_organization_usage",
          },
        ]}
      />
    </>
  );
};

export default SnowflakeCredentials;
