const assert = require('node:assert/strict');

const {
  getDeepWikiApiPort,
  getDeepWikiUiPort,
  normalizeHealthServices,
} = require('../static/app-helpers.js');

assert.equal(getDeepWikiApiPort('native', {}), '8091');
assert.equal(getDeepWikiApiPort('native', { portDeepwiki: '8123' }), '8123');

assert.equal(getDeepWikiUiPort('native', {}), '3001');
assert.equal(getDeepWikiUiPort('native', { deepwikiUiPort: '3111' }), '3111');

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
