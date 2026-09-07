import { useApolloClient } from "@apollo/client";
import { Link, Skeleton } from "@mui/material";
import { FormattedMessage } from "react-intl";
import { Link as RouterLink, useNavigate } from "react-router-dom";
import ActionBar from "components/ActionBar";
import CreateOrganizationConstraintForm from "components/CreateOrganizationConstraintForm";
import PageContentWrapper from "components/PageContentWrapper";
import { GetOrganizationConstraintDocument } from "graphql/__generated__/hooks/restapi";
import OrganizationConstraintsService from "services/OrganizationConstraintsService";
import { TAGGING_POLICIES, getTaggingPolicyUrl } from "urls";
import { TAGGING_POLICY_TYPES } from "utils/constants";
import { buildConstraintParamsFromFormData } from "utils/organizationConstraints/buildConstraintParamsFromFormData";
import { mapConstraintToFormDefaultValues } from "utils/organizationConstraints/mapConstraintToFormDefaultValues";

const EditOrganizationConstraintFormContainer = ({ constraintId }) => {
  const navigate = useNavigate();
  const apolloClient = useApolloClient();
  const { useGetOne, useUpdate } = OrganizationConstraintsService();
  const { isLoading, constraint } = useGetOne(constraintId);
  const { update, isLoading: isUpdateLoading } = useUpdate();

  const navigateAway = () => navigate(getTaggingPolicyUrl(constraintId));
  const types = Object.keys(TAGGING_POLICY_TYPES);

  const actionBar = {
    breadcrumbs: [
      <Link key={1} to={TAGGING_POLICIES} component={RouterLink}>
        <FormattedMessage id="taggingPolicy.taggingPoliciesTitle" />
      </Link>,
      <Link key={2} to={getTaggingPolicyUrl(constraintId)} component={RouterLink}>
        {constraint?.name || <FormattedMessage id="taggingPolicyTitle" />}
      </Link>,
    ],
    title: {
      text: <FormattedMessage id="taggingPolicy.editTaggingPolicyTitle" />,
      dataTestId: "lbl_edit_tagging_policy",
      isLoading,
    },
  };

  return (
    <>
      <ActionBar data={actionBar} />
      <PageContentWrapper>
        {isLoading || !constraint?.id ? (
          <Skeleton height={320} />
        ) : (
          <CreateOrganizationConstraintForm
            types={types}
            isEdit
            defaultValues={mapConstraintToFormDefaultValues(constraint)}
            isLoading={isUpdateLoading}
            navigateAway={navigateAway}
            onSubmit={(formData) => {
              update({
                id: constraintId,
                params: buildConstraintParamsFromFormData(formData, { includeType: false }),
                onSuccess: async () => {
                  await apolloClient.refetchQueries({
                    include: [GetOrganizationConstraintDocument],
                  });
                  navigateAway();
                },
              });
            }}
          />
        )}
      </PageContentWrapper>
    </>
  );
};

export default EditOrganizationConstraintFormContainer;
