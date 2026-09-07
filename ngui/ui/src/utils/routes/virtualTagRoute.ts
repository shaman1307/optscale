import { VIRTUAL_TAG } from "urls";
import BaseRoute from "./baseRoute";

class VirtualTagRoute extends BaseRoute {
  page = "VirtualTag";

  link = VIRTUAL_TAG;
}

export default new VirtualTagRoute();
