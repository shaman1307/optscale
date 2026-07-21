import { UPDATE_DATA_SOURCE } from "api/restapi/actionTypes";
import SnowflakeCostModelForm from "components/forms/SnowflakeCostModelForm";
import { FIELD_NAMES } from "components/forms/SnowflakeCostModelForm/constants";
import { useApiState } from "hooks/useApiState";
import DataSourcesService from "services/DataSourcesService";

const SnowflakeCostModelFormContainer = ({ cloudAccountId, costModel = {}, onSuccess, onCancel }) => {
  const { isLoading } = useApiState(UPDATE_DATA_SOURCE);
  const { useUpdateDataSource } = DataSourcesService();
  const { onUpdate } = useUpdateDataSource();

  return (
    <SnowflakeCostModelForm
      onSubmit={(formData) =>
        onUpdate(cloudAccountId, {
          config: {
            cost_model: {
              credit_price: Number(formData[FIELD_NAMES.CREDIT_PRICE]),
              storage_price_per_tb_month: Number(formData[FIELD_NAMES.STORAGE_PRICE_PER_TB_MONTH]),
            },
          },
        }).then(() => onSuccess())
      }
      onCancel={onCancel}
      creditPrice={costModel.credit_price ?? 0}
      storagePricePerTbMonth={costModel.storage_price_per_tb_month ?? 23}
      isLoading={isLoading}
    />
  );
};

export default SnowflakeCostModelFormContainer;
