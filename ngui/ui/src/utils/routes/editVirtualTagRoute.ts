import { VIRTUAL_TAG_EDIT } from "urls";
import BaseRoute from "./baseRoute";

class EditVirtualTagRoute extends BaseRoute {
  page = "EditVirtualTag";

  link = VIRTUAL_TAG_EDIT;
}

export default new EditVirtualTagRoute();
