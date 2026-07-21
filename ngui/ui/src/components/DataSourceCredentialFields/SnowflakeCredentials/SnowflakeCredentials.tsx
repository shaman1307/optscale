import { FormattedMessage } from "react-intl";
import { TextInput } from "components/forms/common/fields";
import QuestionMark from "components/QuestionMark";

export const FIELD_NAMES = Object.freeze({
  ACCOUNT: "account",
  USER: "user",
  PRIVATE_KEY: "privateKey",
  ROLE: "role",
  WAREHOUSE: "warehouse",
});

const SnowflakeCredentials = ({ readOnlyFields = [] }) => {
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
        required
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
        name={FIELD_NAMES.ROLE}
        defaultValue="ACCOUNTADMIN"
        InputProps={{
          endAdornment: <QuestionMark messageId="snowflakeRoleTooltip" dataTestId="qmark_snowflake_role" />,
        }}
        label={<FormattedMessage id="role" />}
        dataTestId="input_snowflake_role"
      />
    </>
  );
};

export default SnowflakeCredentials;
