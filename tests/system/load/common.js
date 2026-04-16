import http from "k6/http";
import { check } from "k6";

export const config = {
  apiBaseUrl: __ENV.K6_API_BASE_URL || "http://localhost:8000",
  bearerToken: __ENV.K6_BEARER_TOKEN || "",
  activeOrgId: __ENV.K6_ACTIVE_ORG_ID || "",
  notebookId: __ENV.K6_NOTEBOOK_ID || "",
  duration: __ENV.K6_DURATION || "30s",
  vus: Number(__ENV.K6_VUS || "5"),
  chatMessage:
    __ENV.K6_CHAT_MESSAGE ||
    "Summarize the strongest evidence currently available in this notebook.",
};

export function buildHeaders(requestId) {
  const headers = {
    Accept: "application/json",
    "X-Request-Id": requestId,
  };

  if (config.bearerToken) {
    headers.Authorization = `Bearer ${config.bearerToken}`;
  }

  if (config.activeOrgId) {
    headers["X-Active-Organization-Id"] = config.activeOrgId;
  }

  return headers;
}

export function buildRequestId(prefix) {
  return `${prefix}-vu${__VU}-iter${__ITER}`;
}

function getHeader(headers, name) {
  return headers[name] || headers[name.toLowerCase()] || headers[name.toUpperCase()] || null;
}

export function getJson(path, requestId, expectedStatus = 200) {
  const response = http.get(`${config.apiBaseUrl}${path}`, {
    headers: buildHeaders(requestId),
    tags: { path },
  });

  check(response, {
    [`${path} status ${expectedStatus}`]: (current) => current.status === expectedStatus,
    [`${path} echoes x-request-id`]: (current) =>
      getHeader(current.headers, "X-Request-Id") === requestId,
  });

  return response;
}

export function postJson(path, payload, requestId, expectedStatus = 200) {
  const response = http.post(`${config.apiBaseUrl}${path}`, JSON.stringify(payload), {
    headers: {
      ...buildHeaders(requestId),
      "Content-Type": "application/json",
    },
    tags: { path },
  });

  check(response, {
    [`${path} status ${expectedStatus}`]: (current) => current.status === expectedStatus,
    [`${path} echoes x-request-id`]: (current) =>
      getHeader(current.headers, "X-Request-Id") === requestId,
  });

  return response;
}
