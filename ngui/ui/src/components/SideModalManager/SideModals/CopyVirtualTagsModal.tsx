import CopyVirtualTagsContainer from "containers/CopyVirtualTagsContainer";
import BaseSideModal from "./BaseSideModal";

class CopyVirtualTagsModal extends BaseSideModal {
  headerProps = {
    messageId: "copyVirtualTagsToQuarterTitle",
    dataTestIds: {
      title: "lbl_copy_virtual_tags",
      closeButton: "btn_close",
    },
  };

  dataTestId = "smodal_copy_virtual_tags";

  get content() {
    return (
      <CopyVirtualTagsContainer sourceQuarter={this.payload?.sourceQuarter} onCancel={this.closeSideModal} />
    );
  }
}

export default CopyVirtualTagsModal;
