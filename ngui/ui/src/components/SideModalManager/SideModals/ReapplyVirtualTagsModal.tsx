import ReapplyVirtualTagsContainer from "containers/ReapplyVirtualTagsContainer";
import BaseSideModal from "./BaseSideModal";

class ReapplyVirtualTagsModal extends BaseSideModal {
  get headerProps() {
    return {
      messageId: this.payload?.virtualTagId ? "reapplyVirtualTagTitle" : "reapplyVirtualTagsTitle",
      dataTestIds: {
        title: "lbl_reapply_virtual_tags",
        closeButton: "btn_close",
      },
    };
  }

  dataTestId = "smodal_reapply_virtual_tags";

  get content() {
    return (
      <ReapplyVirtualTagsContainer
        onCancel={this.closeSideModal}
        quarter={this.payload?.quarter}
        virtualTagId={this.payload?.virtualTagId}
        virtualTagName={this.payload?.virtualTagName}
      />
    );
  }
}

export default ReapplyVirtualTagsModal;
