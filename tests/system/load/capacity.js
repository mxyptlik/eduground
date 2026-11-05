// k6 load profile for authenticated product traffic. Identity file stays local
// and is never copied into a result artifact.
import http from "k6/http";
import { check, sleep } from "k6";
import { SharedArray } from "k6/data";

const base = __ENV.K6_API_BASE_URL || "http://127.0.0.1:8000";
const workload = __ENV.K6_WORKLOAD || "read";
const identitiesPath = __ENV.K6_IDENTITIES_FILE;
if (!["read", "chat", "mixed"].includes(workload)) throw new Error("K6_WORKLOAD must be read, chat or mixed");
if (!identitiesPath) throw new Error("K6_IDENTITIES_FILE is required for authenticated capacity tests");
const identities = new SharedArray("identities", () => {
  const rows = JSON.parse(open(identitiesPath));
  if (!Array.isArray(rows) || !rows.length || rows.some(
    x => !x.bearer_token || !x.active_org_id || !x.notebook_id)) {
    throw new Error("Identity file must contain bearer_token, active_org_id and notebook_id per user");
  }
  return rows;
});
if (["K6_VUS", "K6_DURATION", "K6_ITERATIONS", "K6_STAGES"].some(key => __ENV[key])) {
  throw new Error("Use PRODUCT_VUS and PRODUCT_DURATION; built-in K6_* options can override the bounded scenario");
}
const vus = Number(__ENV.PRODUCT_VUS || "5");
if (identities.length < vus) throw new Error("Provide at least one distinct identity per VU");
const maxModelCalls = Number(__ENV.K6_MAX_MODEL_CALLS || "0");
if (workload !== "read" && (!Number.isInteger(maxModelCalls) || maxModelCalls < 1)) {
  throw new Error("Chat or mixed workload requires a positive K6_MAX_MODEL_CALLS budget");
}
if (workload !== "read" && __ENV.K6_ALLOW_LIVE_MODEL_CALLS !== "yes") {
  throw new Error("Chat workload requires explicit K6_ALLOW_LIVE_MODEL_CALLS=yes and an independent provider budget check");
}

export const options = {
  ...(workload === "read"
    ? { vus, duration: __ENV.PRODUCT_DURATION || "2m" }
    : { scenarios: { bounded_answers: { executor: "shared-iterations", vus,
        iterations: maxModelCalls, maxDuration: __ENV.PRODUCT_DURATION || "2m" } } }),
  thresholds: {
    http_req_failed: ["rate<0.01"],
    checks: ["rate>0.99"],
    ...(workload === "read" || workload === "mixed" ? { "http_req_duration{name:read}": ["p(95)<1000"] } : {}),
    ...(workload !== "read" ? { "http_req_duration{name:answer}": ["p(95)<30000"] } : {}),
  },
};

function request(profile, path, method = "GET", body = null, name = "read") {
  const response = http.request(method, `${base}${path}`, body ? JSON.stringify(body) : null, {
    headers: {
      Authorization: `Bearer ${profile.bearer_token}`,
      "X-Active-Organization-Id": profile.active_org_id,
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    tags: { name },
    timeout: "35s",
  });
  check(response, { [`${name} succeeded`]: r => r.status >= 200 && r.status < 300 });
  return response;
}

export default function () {
  const profile = identities[__VU - 1];
  if (workload === "read" || (workload === "mixed" && __ITER % 4 !== 0)) {
    request(profile, "/api/notebooks");
    request(profile, `/api/notebooks/${profile.notebook_id}/sources`);
    request(profile, `/api/notebooks/${profile.notebook_id}/notes`);
  } else {
    const session = request(profile, `/api/notebooks/${profile.notebook_id}/chat/sessions`,
      "POST", { title: "Capacity test" }, "session");
    if (session.status === 200 && session.json()?.id) {
      request(profile, `/api/chat/sessions/${session.json().id}/messages`, "POST",
        { content_markdown: __ENV.K6_QUESTION || "What does the course explain?" }, "answer");
    }
  }
  sleep(Number(__ENV.K6_THINK_SECONDS || "2"));
}

export function handleSummary(data) {
  const path = __ENV.K6_SUMMARY_FILE;
  return path ? { [path]: JSON.stringify({ workload, identity_count: identities.length,
    max_model_calls: workload === "read" ? 0 : maxModelCalls,
    provider_mode: __ENV.K6_PROVIDER_MODE || "unspecified", metrics: data.metrics,
    root_group: data.root_group }, null, 2) } : {};
}
