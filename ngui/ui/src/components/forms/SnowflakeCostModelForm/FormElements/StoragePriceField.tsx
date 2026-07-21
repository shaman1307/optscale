import InputAdornment from "@mui/material/InputAdornment";
import { FormattedMessage, useIntl } from "react-intl";
import { NumberInput } from "components/forms/common/fields";
import { useOrganizationInfo } from "hooks/useOrganizationInfo";
import { isPositiveNumberOrZero, costModelValueMaxFractionDigitsValidation } from "utils/validation";
import { FIELD_NAMES } from "../constants";

const FIELD_NAME = FIELD_NAMES.STORAGE_PRICE_PER_TB_MONTH;

const StoragePriceField = () => {
  const { currencySymbol } = useOrganizationInfo();
  const intl = useIntl();

  return (
    <NumberInput
      required
      name={FIELD_NAME}
      label={<FormattedMessage id="storagePricePerTbMonth" />}
      InputProps={{
        startAdornment: <InputAdornment position="start">{currencySymbol}</InputAdornment>,
      }}
      min={0}
      validate={{
        positiveNumber: (value) => (isPositiveNumberOrZero(value) ? true : intl.formatMessage({ id: "positiveNumber" })),
        fractionDigits: costModelValueMaxFractionDigitsValidation,
      }}
      dataTestId="input_storage_price"
    />
  );
};

export default StoragePriceField;
