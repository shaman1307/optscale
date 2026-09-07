import Protector from "components/Protector";
import CreateVirtualTagFormContainer from "containers/CreateVirtualTagFormContainer";

const CreateVirtualTag = () => (
  <Protector allowedActions={["EDIT_PARTNER"]}>
    <CreateVirtualTagFormContainer />
  </Protector>
);

export default CreateVirtualTag;
