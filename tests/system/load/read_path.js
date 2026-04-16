import { sleep } from "k6";
import { config, buildRequestId, getJson } from "./common.js";

export const options = {
  vus: config.vus,
  duration: config.duration,
  thresholds: {
    http_req_failed: ["rate<0.05"],
    http_req_duration: ["p(95)<1500"],
    checks: ["rate>0.99"],
  },
};

export default function readPathScenario() {
  getJson("/health", buildRequestId("health"), 200);

  if (config.bearerToken) {
    getJson("/api/auth/me", buildRequestId("auth-me"), 200);
  }

  if (config.bearerToken && config.notebookId) {
    getJson("/api/notebooks", buildRequestId("notebooks"), 200);
    getJson(
      `/api/notebooks/${config.notebookId}/sources`,
      buildRequestId("sources"),
      200,
    );
    getJson(
      `/api/notebooks/${config.notebookId}/notes`,
      buildRequestId("notes"),
      200,
    );
    getJson(
      `/api/notebooks/${config.notebookId}/quizzes`,
      buildRequestId("quizzes"),
      200,
    );
    getJson(
      `/api/notebooks/${config.notebookId}/analytics`,
      buildRequestId("analytics"),
      200,
    );
  }

  sleep(1);
}
