import { useEffect } from "react";
import { Autocomplete, Box, TextField, ToggleButton, ToggleButtonGroup } from "@mui/material";
import { FormattedMessage, useIntl } from "react-intl";
import { useDispatch } from "react-redux";
import { getInvoiceMonths } from "api";
import { GET_INVOICE_MONTHS } from "api/restapi/actionTypes";
import RangePickerFormContainer from "containers/RangePickerFormContainer";
import { useApiData } from "hooks/useApiData";
import { useApiState } from "hooks/useApiState";
import { useHasGcpDataSource } from "hooks/useHasGcpDataSource";
import { useOrganizationInfo } from "hooks/useOrganizationInfo";
import { COST_PERIOD_BILLING, COST_PERIOD_DATE, formatInvoiceMonthLabel } from "utils/costPeriod";

const CostPeriodSelector = ({
  periodType,
  onPeriodTypeChange,
  onApplyDates,
  initialStartDateValue,
  initialEndDateValue,
  rangeType,
  definedRanges,
  pdfId,
  invoiceMonths = [],
  onInvoiceMonthsChange,
  forceDateRange = false,
  minDate,
  maxDate,
}) => {
  const intl = useIntl();
  const dispatch = useDispatch();
  const { organizationId } = useOrganizationInfo();
  const hasGcp = useHasGcpDataSource();

  const { isLoading, shouldInvoke } = useApiState(GET_INVOICE_MONTHS, { organizationId });
  const {
    apiData: { invoice_months: availableMonths = [] },
  } = useApiData(GET_INVOICE_MONTHS, { invoice_months: [] });

  useEffect(() => {
    if (hasGcp && shouldInvoke) {
      dispatch(getInvoiceMonths(organizationId));
    }
  }, [dispatch, hasGcp, organizationId, shouldInvoke]);

  const rangePicker = (
    <RangePickerFormContainer
      onApply={onApplyDates}
      initialStartDateValue={initialStartDateValue}
      initialEndDateValue={initialEndDateValue}
      rangeType={rangeType}
      definedRanges={definedRanges}
      pdfId={pdfId}
      minDate={minDate}
      maxDate={maxDate}
    />
  );

  if (!hasGcp || forceDateRange) {
    return rangePicker;
  }

  return (
    <Box display="flex" alignItems="center" flexWrap="wrap" gap={1}>
      <ToggleButtonGroup
        exclusive
        size="small"
        value={periodType}
        onChange={(_, value) => {
          if (value) {
            onPeriodTypeChange(value);
          }
        }}
      >
        <ToggleButton value={COST_PERIOD_DATE}>
          <FormattedMessage id="dateRange" />
        </ToggleButton>
        <ToggleButton value={COST_PERIOD_BILLING}>
          <FormattedMessage id="billingMonth" />
        </ToggleButton>
      </ToggleButtonGroup>
      {periodType === COST_PERIOD_BILLING ? (
        <Autocomplete
          multiple
          size="small"
          sx={{ minWidth: 260 }}
          options={availableMonths}
          value={invoiceMonths}
          loading={isLoading}
          disableCloseOnSelect
          getOptionLabel={(option) => formatInvoiceMonthLabel(option)}
          onChange={(_, value) => onInvoiceMonthsChange(value)}
          renderInput={(params) => (
            <TextField
              {...params}
              label={intl.formatMessage({ id: "billingMonth" })}
              placeholder={intl.formatMessage({ id: "selectBillingMonths" })}
            />
          )}
        />
      ) : (
        rangePicker
      )}
    </Box>
  );
};

export default CostPeriodSelector;
