import KeyValueLabel from "components/KeyValueLabel/KeyValueLabel";
import { SNOWFLAKE } from "utils/constants";
import { SnowflakePropertiesProps } from "./types";

const SnowflakeProperties = ({ accountId, config, createdAt }: SnowflakePropertiesProps) => {
  const { account, user, role, warehouse, backup_warehouse: backupWarehouse, region } = config;

  return (
    <>
      <KeyValueLabel
        keyMessageId="connectedAt"
        value={createdAt}
        dataTestIds={{
          key: "p_connected_at_id",
          value: "p_connected_at_value",
        }}
      />
      <KeyValueLabel
        keyMessageId="accountId"
        value={accountId}
        dataTestIds={{
          key: `p_${SNOWFLAKE}_id`,
          value: `p_${SNOWFLAKE}_value`,
        }}
      />
      <KeyValueLabel
        keyMessageId="account"
        value={account}
        dataTestIds={{
          key: "p_account_key",
          value: "p_account_value",
        }}
      />
      {!!region && (
        <KeyValueLabel
          keyMessageId="region"
          value={region}
          dataTestIds={{
            key: "p_region_key",
            value: "p_region_value",
          }}
        />
      )}
      <KeyValueLabel
        keyMessageId="user"
        value={user}
        dataTestIds={{
          key: "p_user_key",
          value: "p_user_value",
        }}
      />
      <KeyValueLabel
        keyMessageId="role"
        value={role}
        dataTestIds={{
          key: "p_role_key",
          value: "p_role_value",
        }}
      />
      <KeyValueLabel
        keyMessageId="warehouse"
        value={warehouse}
        dataTestIds={{
          key: "p_warehouse_key",
          value: "p_warehouse_value",
        }}
      />
      {!!backupWarehouse && (
        <KeyValueLabel
          keyMessageId="backupWarehouse"
          value={backupWarehouse}
          dataTestIds={{
            key: "p_backup_warehouse_key",
            value: "p_backup_warehouse_value",
          }}
        />
      )}
    </>
  );
};

export default SnowflakeProperties;
