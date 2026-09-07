import { handleSuccess } from "api/actionCreators";
import { MINUTE } from "api/constants";
import { apiAction, getApiUrl, hashParams } from "api/utils";
import {
  CREATE_USER,
  GET_TOKEN,
  GET_USER,
  SET_USER,
  RESET_PASSWORD,
  GET_POOL_ALLOWED_ACTIONS,
  GET_RESOURCE_ALLOWED_ACTIONS,
  SET_ALLOWED_ACTIONS,
  SIGN_IN,
  UPDATE_USER,
  SET_TOKEN,
} from "./actionTypes";
import { onSuccessSignIn } from "./handlers";

export const API_URL = getApiUrl("auth");

// Edge/nginx returns 414 when all pool/resource ids are sent in one query string
// (~200 UUIDs ≈ 8.5KB). Keep chunks safely under common header buffer limits.
const ALLOWED_ACTIONS_CHUNK_SIZE = 40;

const chunkIds = (ids) => {
  const list = ids == null ? [] : Array.isArray(ids) ? ids : [ids];
  const uniqueIds = [...new Set(list.filter(Boolean))];
  const chunks = [];
  for (let i = 0; i < uniqueIds.length; i += ALLOWED_ACTIONS_CHUNK_SIZE) {
    chunks.push(uniqueIds.slice(i, i + ALLOWED_ACTIONS_CHUNK_SIZE));
  }
  return chunks;
};

const getAllowedActionsInChunks =
  ({ ids, paramKey, label }) =>
  (dispatch) => {
    const chunks = chunkIds(ids);
    if (chunks.length === 0) {
      return Promise.resolve();
    }

    // Sequential chunks with allowMultipleRequests: a parallel re-fetch of the same
    // label would otherwise cancel an in-flight chunk (requestManager cancels by label).
    return chunks.reduce(
      (chain, chunk) =>
        chain.then(() =>
          dispatch(
            apiAction({
              url: `${API_URL}/allowed_actions`,
              method: "GET",
              onSuccess: handleSuccess(SET_ALLOWED_ACTIONS),
              label,
              hash: hashParams(chunk),
              params: { [paramKey]: chunk },
              ttl: 30 * MINUTE,
              allowMultipleRequests: true,
            })
          )
        ),
      Promise.resolve()
    );
  };

export const getToken = ({ email, password, code }) =>
  apiAction({
    url: `${API_URL}/tokens`,
    onSuccess: handleSuccess(SET_TOKEN),
    label: GET_TOKEN,
    params: { email, password, verification_code: code },
  });

export const signIn = (provider, params) =>
  apiAction({
    url: `${API_URL}/signin`,
    onSuccess: onSuccessSignIn,
    label: SIGN_IN,
    params: { provider, ...params },
  });

export const createUser = (name, email, password) =>
  apiAction({
    url: `${API_URL}/users`,
    onSuccess: handleSuccess(CREATE_USER),
    label: CREATE_USER,
    params: { display_name: name, email, password },
  });

export const updateUser = (userId, params = {}) =>
  apiAction({
    url: `${API_URL}/users/${userId}`,
    method: "PATCH",
    label: UPDATE_USER,
    params: { display_name: params.name, password: params.password },
  });

export const getUser = (userId) =>
  apiAction({
    url: `${API_URL}/users/${userId}`,
    method: "GET",
    onSuccess: handleSuccess(SET_USER),
    label: GET_USER,
    ttl: 30 * MINUTE,
  });

export const getResourceAllowedActions = (params) =>
  getAllowedActionsInChunks({
    ids: params,
    paramKey: "cloud_resource",
    label: GET_RESOURCE_ALLOWED_ACTIONS,
  });

export const getPoolAllowedActions = (params) =>
  getAllowedActionsInChunks({
    ids: params,
    paramKey: "pool",
    label: GET_POOL_ALLOWED_ACTIONS,
  });

export const resetPassword = (email) =>
  apiAction({
    url: `${API_URL}/restore_password`,
    method: "POST",
    label: RESET_PASSWORD,
    params: { email },
  });
