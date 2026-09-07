import Protector from "components/Protector";
import EditVirtualTagFormContainer from "containers/EditVirtualTagFormContainer";

const EditVirtualTag = () => (
  <Protector allowedActions={["EDIT_PARTNER"]}>
    <EditVirtualTagFormContainer />
  </Protector>
);

export default EditVirtualTag;
