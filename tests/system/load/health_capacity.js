import http from 'k6/http';
import { check, sleep } from 'k6';
export const options = {
  scenarios: { health: { executor: 'constant-vus', vus: Number(__ENV.K6_VUS),
    duration: __ENV.K6_DURATION, gracefulStop: '3s' } },
  thresholds: { http_req_failed: ['rate==0'], checks: ['rate==1'], http_req_duration: ['p(95)<1500'] },
};
export default function () {
  const response = http.get(`${__ENV.K6_API_BASE_URL}/health`, { timeout: '5s' });
  check(response, { 'healthy response': r => r.status === 200 && r.json('status') === 'ok' });
  sleep(1);
}
