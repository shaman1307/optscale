import { FIELD_NAMES } from "./constants";
import { FormValues } from "./types";

export const getDefaultValues = ({
  creditPrice,
  storagePricePerTbMonth,
}: {
  creditPrice: number;
  storagePricePerTbMonth: number;
}): FormValues => ({
  [FIELD_NAMES.CREDIT_PRICE]: creditPrice != null ? String(creditPrice) : "0",
  [FIELD_NAMES.STORAGE_PRICE_PER_TB_MONTH]:
    storagePricePerTbMonth != null ? String(storagePricePerTbMonth) : "23",
});
