import ToggleImportSchedulerContainer from "containers/ToggleImportSchedulerContainer";
import BaseSideModal from "./BaseSideModal";

class ToggleImportSchedulerModal extends BaseSideModal {
  get headerProps() {
    const currentlyEnabled = this.payload?.currentlyEnabled === true;
    return {
      messageId: currentlyEnabled ? "stopIncrementalScheduler" : "startIncrementalScheduler",
      color: currentlyEnabled ? "error" : "success",
      dataTestIds: {
        title: "lbl_toggle_import_scheduler",
        closeButton: "btn_close",
      },
    };
  }

  dataTestId = "smodal_toggle_import_scheduler";

  get content() {
    return (
      <ToggleImportSchedulerContainer
        currentlyEnabled={this.payload?.currentlyEnabled === true}
        closeSideModal={this.closeSideModal}
      />
    );
  }
}

export default ToggleImportSchedulerModal;
