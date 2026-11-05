// Repeated local health profile. Use HEALTH_* variables so k6's built-in
// K6_VUS/K6_DURATION overrides cannot silently replace the ramp scenario.
import http from "k6/http";
import { check, sleep } from "k6";
import { Counter } from "k6/metrics";

const target = Number(__ENV.HEALTH_TARGET_VUS || "243");
const mode = __ENV.HEALTH_START_MODE || "constant";
const plateau = __ENV.HEALTH_PLATEAU || "20s";
const ramp = __ENV.HEALTH_RAMP || "5s";
if (!Number.isInteger(target) || target < 1) throw new Error("HEALTH_TARGET_VUS must be positive");
if (!["constant", "ramp"].includes(mode)) throw new Error("HEALTH_START_MODE must be constant or ramp");

export const firstRequestFailures = new Counter("first_request_failures");
export const laterRequestFailures = new Counter("later_request_failures");

export const options = {
  scenarios: mode === "constant"
    ? { health: { executor: "constant-vus", vus: target, duration: plateau, gracefulStop: "3s" } }
    : { health: { executor: "ramping-vus", startVUs: 0,
        stages: [{ duration: ramp, target }, { duration: plateau, target }],
        gracefulRampDown: "3s", gracefulStop: "3s" } },
  thresholds: {
    http_req_failed: ["rate==0"],
    checks: ["rate==1"],
    http_req_duration: ["p(95)<1500"],
  },
};

export default function () {
  const response = http.get(`${__ENV.HEALTH_API_BASE_URL}/health`, { timeout: "5s" });
  const passed = response.status === 200 && response.json("status") === "ok";
  check(response, { "healthy response": () => passed });
  if (!passed) {
    if (__ITER === 0) firstRequestFailures.add(1);
    else laterRequestFailures.add(1);
  }
  sleep(1);
}
