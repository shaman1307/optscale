import { FormProvider, useForm } from "react-hook-form";
import UpdateCostModelWarning from "components/UpdateCostModelWarning/UpdateCostModelWarning";
import { COST_MODEL_TYPES } from "utils/constants";
import { CreditPriceField, FormButtons, StoragePriceField } from "./FormElements";
import { SnowflakeCostModelFormProps, FormValues } from "./types";
import { getDefaultValues } from "./utils";

const SnowflakeCostModelForm = ({
  creditPrice,
  storagePricePerTbMonth,
  onSubmit,
  onCancel,
  isLoading = false,
}: SnowflakeCostModelFormProps) => {
  const methods = useForm<FormValues>({
    defaultValues: getDefaultValues({
      creditPrice,
      storagePricePerTbMonth,
    }),
  });

  const { handleSubmit } = methods;

  return (
    <FormProvider {...methods}>
      <form onSubmit={handleSubmit(onSubmit)} noValidate>
        <UpdateCostModelWarning costModelType={COST_MODEL_TYPES.SNOWFLAKE} dataTestId="p_recalculation_message" />
        <CreditPriceField />
        <StoragePriceField />
        <FormButtons onCancel={onCancel} isLoading={isLoading} />
      </form>
    </FormProvider>
  );
};

export default SnowflakeCostModelForm;
