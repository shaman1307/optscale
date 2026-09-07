import { perspectiveSchema } from "schemas";
import { validateSchema } from "./validation";

/** Kept separate from validation helpers to avoid a cycle:
 * filterConfigs → validation → schemas → filterConfigs
 */
export const validatePerspectiveSchema = (data, options = {}) => validateSchema(data, perspectiveSchema, options);
