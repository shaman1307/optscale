import Protector from "components/Protector";
import CreateVirtualTagRuleFormContainer from "containers/CreateVirtualTagRuleFormContainer";

const CreateVirtualTagRule = () => (
  <Protector allowedActions={["EDIT_PARTNER"]}>
    <CreateVirtualTagRuleFormContainer />
  </Protector>
);

export default CreateVirtualTagRule;
