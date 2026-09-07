import { VIRTUAL_TAG_RULE_EDIT } from "urls";
import BaseRoute from "./baseRoute";

class EditVirtualTagRuleRoute extends BaseRoute {
  page = "EditVirtualTagRule";

  link = VIRTUAL_TAG_RULE_EDIT;
}

export default new EditVirtualTagRuleRoute();
