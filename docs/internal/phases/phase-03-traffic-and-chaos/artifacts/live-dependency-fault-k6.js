import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  stages: [
    { duration: '5s', target: 2 },
    { duration: '10s', target: 2 },
    { duration: '5s', target: 0 },
  ],
};

const targetUrl = __ENV.TARGET_URL || 'http://localhost:8080/dependency';

export default function () {
  const response = http.get(targetUrl);
  check(response, {
    'dependency path returns below 500': (r) => r.status < 500,
  });
  sleep(0.1);
}
