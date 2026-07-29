import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const settings = readFileSync(new URL("../src/app/settings/page.tsx", import.meta.url), "utf8");
const api = readFileSync(new URL("../src/lib/api.ts", import.meta.url), "utf8");
const types = readFileSync(new URL("../src/lib/types.ts", import.meta.url), "utf8");

test("Phase 7 settings migration preview stays read-only and is rendered", () => {
  assert.match(types, /interface DeploymentNetworkMigrationPreview/);
  assert.match(types, /contract_version:\s*number/);
  assert.match(types, /source:\s*"network_mode"\s*\|\s*"legacy_intranet_network_mode"/);
  assert.match(types, /migration_preview:\s*DeploymentNetworkMigrationPreview/);
  assert.match(api, /getNetworkPolicy/);
  assert.match(settings, /旧版网络模式迁移预览/);
  assert.match(types, /legacy_intranet_network_mode/);
  assert.match(settings, /migration\.admin_guidance/);
  assert.doesNotMatch(api, /(?:update|create|delete)NetworkMigration/);
});

test("Phase 7 settings handles an unknown migration contract version safely", () => {
  assert.match(settings, /migration\.contract_version !== 1/);
  assert.match(settings, /不支持的设置迁移契约版本/);
  assert.match(settings, /当前部署设置保持只读/);
  assert.match(settings, /联系管理员升级后端或确认部署配置/);
});
