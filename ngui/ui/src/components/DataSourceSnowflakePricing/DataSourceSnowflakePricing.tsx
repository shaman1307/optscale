import SettingsIcon from "@mui/icons-material/Settings";
import { Box } from "@mui/material";
import Button from "components/Button";
import CostModelFormattedMoney from "components/CostModelFormattedMoney";
import KeyValueLabel from "components/KeyValueLabel/KeyValueLabel";
import { UpdateSnowflakeCostModelModal } from "components/SideModalManager/SideModals";
import { useOpenSideModal } from "hooks/useOpenSideModal";
import { SPACING_2 } from "utils/layouts";

const DataSourceSnowflakePricing = ({ cloudAccountId, costModel = {} }) => {
  const openSideModal = useOpenSideModal();
  const creditPrice = costModel.credit_price ?? 0;
  const storagePricePerTbMonth = costModel.storage_price_per_tb_month ?? 23;

  return (
    <Box display="flex" flexDirection="column" gap={SPACING_2}>
      <Box>
        <Button
          messageId="updateCostModel"
          startIcon={<SettingsIcon fontSize="small" />}
          variant="text"
          color="primary"
          onClick={() => openSideModal(UpdateSnowflakeCostModelModal, { cloudAccountId, costModel })}
          dataTestId="btn_update_snowflake_cost_model"
        />
      </Box>
      <KeyValueLabel
        keyMessageId="creditPrice"
        value={<CostModelFormattedMoney value={creditPrice} />}
        dataTestIds={{ key: "p_credit_price_key", value: "p_credit_price_value" }}
      />
      <KeyValueLabel
        keyMessageId="storagePricePerTbMonth"
        value={<CostModelFormattedMoney value={storagePricePerTbMonth} />}
        dataTestIds={{ key: "p_storage_price_key", value: "p_storage_price_value" }}
      />
    </Box>
  );
};

export default DataSourceSnowflakePricing;
