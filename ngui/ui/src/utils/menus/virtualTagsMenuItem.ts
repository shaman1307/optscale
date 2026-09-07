import LabelOutlinedIcon from "@mui/icons-material/LabelOutlined";
import { VIRTUAL_TAG_CREATE } from "urls";
import { OPTSCALE_CAPABILITY } from "utils/constants";
import virtualTagRoute from "utils/routes/virtualTagRoute";
import virtualTags from "utils/routes/virtualTagsRoute";
import BaseMenuItem from "./baseMenuItem";

class VirtualTagsMenuItem extends BaseMenuItem {
  route = virtualTags;

  messageId = "virtualTags";

  dataTestId = "btn_virtual_tags";

  icon = LabelOutlinedIcon;

  capability = OPTSCALE_CAPABILITY.FINOPS;

  isActive = (currentPath) =>
    currentPath.startsWith(this.route.link) ||
    currentPath.startsWith(virtualTagRoute.link) ||
    currentPath.startsWith(VIRTUAL_TAG_CREATE);
}

export default new VirtualTagsMenuItem();
