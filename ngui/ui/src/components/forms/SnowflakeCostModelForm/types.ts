import { FIELD_NAMES } from "./constants";

export type FormButtonsProps = {
  onCancel: () => void;
  isLoading?: boolean;
};

export type FormValues = {
  [FIELD_NAMES.CREDIT_PRICE]: string;
  [FIELD_NAMES.STORAGE_PRICE_PER_TB_MONTH]: string;
};

export type SnowflakeCostModelFormProps = {
  creditPrice: number;
  storagePricePerTbMonth: number;
  onSubmit: (data: FormValues) => void;
  onCancel: () => void;
  isLoading?: boolean;
};
