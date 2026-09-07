import { VIRTUAL_TAGS } from "urls";
import BaseRoute from "./baseRoute";

class VirtualTagsRoute extends BaseRoute {
  page = "VirtualTags";

  link = VIRTUAL_TAGS;
}

export default new VirtualTagsRoute();
