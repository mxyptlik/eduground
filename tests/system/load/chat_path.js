import { check, sleep } from "k6";
import { config, buildRequestId, postJson } from "./common.js";

export const options = {
  vus: config.vus,
  duration: config.duration,
  thresholds: {
    http_req_failed: ["rate<0.05"],
    http_req_duration: ["p(95)<4000"],
    checks: ["rate>0.99"],
  },
};

export default function chatPathScenario() {
  if (!config.bearerToken || !config.notebookId) {
    throw new Error("K6_BEARER_TOKEN and K6_NOTEBOOK_ID are required for chat_path.js");
  }

  const createSessionResponse = postJson(
    `/api/notebooks/${config.notebookId}/chat/sessions`,
    { title: "k6 system test session" },
    buildRequestId("chat-session"),
    200,
  );
  const sessionPayload = createSessionResponse.json();
  check(sessionPayload, {
    "chat session id present": (payload) => Boolean(payload?.id),
  });

  const chatSessionId = sessionPayload.id;
  postJson(
    `/api/chat/sessions/${chatSessionId}/messages`,
    { content_markdown: config.chatMessage },
    buildRequestId("chat-message"),
    200,
  );

  sleep(1);
}
