import SnowflakeCostModelFormContainer from "containers/SnowflakeCostModelFormContainer";
import BaseSideModal from "./BaseSideModal";

class UpdateSnowflakeCostModelModal extends BaseSideModal {
  headerProps = {
    messageId: "updateCostModelTitle",
    color: "primary",
    dataTestIds: {
      title: "lbl_update_snowflake_cost_model",
      closeButton: "btn_close",
    },
  };

  dataTestId = "smodal_update_snowflake_cost_model";

  get content() {
    return (
      <SnowflakeCostModelFormContainer
        cloudAccountId={this.payload?.cloudAccountId}
        costModel={this.payload?.costModel}
        onSuccess={this.closeSideModal}
        onCancel={this.closeSideModal}
      />
    );
  }
}

export default UpdateSnowflakeCostModelModal;
