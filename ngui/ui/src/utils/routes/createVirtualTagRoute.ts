import { VIRTUAL_TAG_CREATE } from "urls";
import BaseRoute from "./baseRoute";

class CreateVirtualTagRoute extends BaseRoute {
  page = "CreateVirtualTag";

  link = VIRTUAL_TAG_CREATE;
}

export default new CreateVirtualTagRoute();
