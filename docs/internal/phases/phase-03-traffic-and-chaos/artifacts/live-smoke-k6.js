import http from 'k6/http';
import { check } from 'k6';

export const options = {
  stages: [
    { duration: '5s', target: 2 },
    { duration: '5s', target: 0 },
  ],
};

const targetUrl = __ENV.TARGET_URL || 'http://localhost:8080/healthz';

export default function () {
  const response = http.get(targetUrl);
  check(response, {
    'status is 200': (r) => r.status === 200,
  });
}
