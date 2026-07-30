import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const settingsSource = readFileSync(join(root, "../src/app/settings/page.tsx"), "utf8");
const apiSource = readFileSync(join(root, "../src/lib/api.ts"), "utf8");
const typeSource = readFileSync(join(root, "../src/lib/types.ts"), "utf8");

test("settings reads the CodeTalk runtime network posture through a read-only API", () => {
  assert.match(typeSource, /interface DeploymentNetworkPolicy/);
  assert.match(typeSource, /mode:\s*"developer"\s*\|\s*"intranet"\s*\|\s*"strict_compliance"/);
  assert.match(typeSource, /boundary:\s*"none"\s*\|\s*"approved_proxy_gateway"\s*\|\s*"deployment_egress_policy"/);
  assert.match(typeSource, /approved_proxy_config_id/);
  assert.match(typeSource, /cli_block_reason/);
  assert.match(apiSource, /getNetworkPolicy:\s*\(\)\s*=>\s*request<DeploymentNetworkPolicy>\("\/api\/settings\/network-policy"\)/);
  assert.doesNotMatch(apiSource, /(?:update|create|delete)NetworkPolicy\s*:/);
});

test("settings renders a compact passthrough runtime panel without boundary setup language", () => {
  assert.match(settingsSource, /运行环境网络/);
  assert.match(settingsSource, /CodeTalk 不要求配置出站边界/);
  assert.match(settingsSource, /运行环境直连/);
  assert.match(settingsSource, /模型访问/);
  assert.match(settingsSource, /CLI Agent/);
  assert.match(settingsSource, /进程启动/);
  assert.match(settingsSource, /直接启动/);
  assert.match(settingsSource, /沿用运行环境/);
  assert.match(settingsSource, /不做安全裁决/);
  assert.doesNotMatch(settingsSource, /最终受部署批准策略约束/);
});

test("settings never renders deployment proxy credentials or a raw proxy endpoint", () => {
  assert.doesNotMatch(settingsSource, /approved_proxy_url/);
  assert.doesNotMatch(settingsSource, /approved_proxy_target/);
  assert.doesNotMatch(settingsSource, /approved_proxy_config_id/);
  assert.doesNotMatch(settingsSource, /approved_no_proxy/);
  assert.match(typeSource, /approved_proxy_config_id/);
  assert.match(typeSource, /approved_no_proxy/);
});

test("agent runtime networking remains a capability hint and never advertises sandbox gating", () => {
  assert.match(typeSource, /requires_network:\s*boolean/);
  assert.match(settingsSource, /requires_network:\s*true/);
  assert.match(settingsSource, /网络访问方式/);
  assert.match(settingsSource, /联网 Agent/);
  assert.match(settingsSource, /离线 Agent/);
  assert.doesNotMatch(settingsSource, /需要批准边界/);
  assert.doesNotMatch(settingsSource, /OS 网络隔离/);
  assert.doesNotMatch(settingsSource, /sandbox-exec|bubblewrap/);
  assert.match(settingsSource, /updateAgentRuntimeForm\("requires_network",\s*event\.target\.value === "networked"\)/);
  assert.match(settingsSource, /runtime\.requires_network !== false/);
  assert.match(settingsSource, /htmlFor="agent-runtime-name"/);
  assert.match(settingsSource, /id="agent-runtime-network-access"/);
});

test("raw network deny reasons are not the visible LLM connection result", () => {
  assert.match(settingsSource, /function userFacingLlmTestResult/);
  assert.match(settingsSource, /CodeTalk 不拦截模型地址/);
  assert.match(settingsSource, /setTestResult\(userFacingLlmTestResult\(result\.message\)\)/);
});
