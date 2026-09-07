import { TAGGING_POLICY_EDIT } from "urls";
import BaseRoute from "./baseRoute";

class EditTaggingPolicyRoute extends BaseRoute {
  page = "EditOrganizationConstraint";

  link = TAGGING_POLICY_EDIT;
}

export default new EditTaggingPolicyRoute();
