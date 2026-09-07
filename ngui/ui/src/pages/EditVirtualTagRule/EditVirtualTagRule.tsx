import Protector from "components/Protector";
import EditVirtualTagRuleFormContainer from "containers/EditVirtualTagRuleFormContainer";

const EditVirtualTagRule = () => (
  <Protector allowedActions={["EDIT_PARTNER"]}>
    <EditVirtualTagRuleFormContainer />
  </Protector>
);

export default EditVirtualTagRule;
