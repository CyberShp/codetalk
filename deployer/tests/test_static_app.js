const assert = require('node:assert/strict');

const {
  normalizeHealthServices,
} = require('../static/app-helpers.js');

assert.deepEqual(
  normalizeHealthServices([
    { name: 'backend', healthy: true },
    { healthy: false },
  ]),
  [
    ['backend', { name: 'backend', healthy: true }],
    ['service-2', { healthy: false }],
  ],
);

assert.deepEqual(
  normalizeHealthServices({
    backend: { healthy: true },
    gitnexus: { healthy: false },
  }),
  [
    ['backend', { healthy: true }],
    ['gitnexus', { healthy: false }],
  ],
);

console.log('static app helper checks passed');
