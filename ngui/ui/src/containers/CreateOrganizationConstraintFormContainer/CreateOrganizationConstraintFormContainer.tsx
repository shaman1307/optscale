import { useNavigate } from "react-router-dom";
import CreateOrganizationConstraintForm from "components/CreateOrganizationConstraintForm";
import OrganizationConstraintsService from "services/OrganizationConstraintsService";
import { buildConstraintParamsFromFormData } from "utils/organizationConstraints/buildConstraintParamsFromFormData";

const CreateOrganizationConstraintFormContainer = ({ navigateAwayLink, types }) => {
  const navigate = useNavigate();
  const navigateAway = () => navigate(navigateAwayLink);

  const { useCreate } = OrganizationConstraintsService();

  const { create } = useCreate();

  return (
    <CreateOrganizationConstraintForm
      types={types}
      navigateAway={navigateAway}
      onSubmit={(formData) => {
        create({
          params: buildConstraintParamsFromFormData(formData),
          onSuccess: navigateAway,
        });
      }}
    />
  );
};

export default CreateOrganizationConstraintFormContainer;
