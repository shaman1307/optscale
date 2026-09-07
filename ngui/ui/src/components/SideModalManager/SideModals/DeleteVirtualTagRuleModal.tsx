import DeleteVirtualTagRuleContainer from "containers/DeleteVirtualTagRuleContainer";
import BaseSideModal from "./BaseSideModal";

class DeleteVirtualTagRuleModal extends BaseSideModal {
  headerProps = {
    messageId: "deleteVirtualTagRuleTitle",
    color: "error",
    dataTestIds: {
      title: "lbl_delete_virtual_tag_rule",
      closeButton: "btn_close",
    },
  };

  dataTestId = "smodal_delete_virtual_tag_rule";

  get content() {
    return (
      <DeleteVirtualTagRuleContainer
        ruleId={this.payload?.ruleId}
        ruleName={this.payload?.ruleName}
        onCancel={this.closeSideModal}
      />
    );
  }
}

export default DeleteVirtualTagRuleModal;
