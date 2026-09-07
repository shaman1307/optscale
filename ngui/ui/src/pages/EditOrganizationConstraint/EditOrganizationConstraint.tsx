import { useParams } from "react-router-dom";
import Protector from "components/Protector";
import EditOrganizationConstraintFormContainer from "containers/EditOrganizationConstraintFormContainer";

const EditOrganizationConstraint = () => {
  const { taggingPolicyId } = useParams();

  return (
    <Protector allowedActions={["EDIT_PARTNER"]}>
      <EditOrganizationConstraintFormContainer constraintId={taggingPolicyId} />
    </Protector>
  );
};

export default EditOrganizationConstraint;
