import http from 'k6/http';
import { check } from 'k6';

export const options = {
  "stages": [
    {
      "duration": "1m",
      "target": 5
    },
    {
      "duration": "2m",
      "target": 15
    },
    {
      "duration": "1m",
      "target": 0
    }
  ]
};

const targetUrl = __ENV.TARGET_URL || "http://127.0.0.1:22036/dependency";

export default function () {
  const response = http.get(targetUrl);
  check(response, {
    'status is below 500': (r) => r.status < 500,
  });
}
