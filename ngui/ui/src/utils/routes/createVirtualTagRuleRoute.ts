import { VIRTUAL_TAG_RULE_CREATE } from "urls";
import BaseRoute from "./baseRoute";

class CreateVirtualTagRuleRoute extends BaseRoute {
  page = "CreateVirtualTagRule";

  link = VIRTUAL_TAG_RULE_CREATE;
}

export default new CreateVirtualTagRuleRoute();
