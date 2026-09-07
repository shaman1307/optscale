import DeleteVirtualTagContainer from "containers/DeleteVirtualTagContainer";
import BaseSideModal from "./BaseSideModal";

class DeleteVirtualTagModal extends BaseSideModal {
  headerProps = {
    messageId: "deleteVirtualTagTitle",
    color: "error",
    dataTestIds: {
      title: "lbl_delete_virtual_tag",
      closeButton: "btn_close",
    },
  };

  dataTestId = "smodal_delete_virtual_tag";

  get content() {
    return (
      <DeleteVirtualTagContainer
        virtualTagId={this.payload?.virtualTagId}
        virtualTagName={this.payload?.virtualTagName}
        onCancel={this.closeSideModal}
      />
    );
  }
}

export default DeleteVirtualTagModal;
