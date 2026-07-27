import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const settingsSource = readFileSync(join(root, "../src/app/settings/page.tsx"), "utf8");
const apiSource = readFileSync(join(root, "../src/lib/api.ts"), "utf8");
const typeSource = readFileSync(join(root, "../src/lib/types.ts"), "utf8");

test("settings reads the deployment-owned network policy through a read-only API", () => {
  assert.match(typeSource, /interface DeploymentNetworkPolicy/);
  assert.match(typeSource, /mode:\s*"developer"\s*\|\s*"intranet"\s*\|\s*"strict_compliance"/);
  assert.match(typeSource, /boundary:\s*"none"\s*\|\s*"approved_proxy_gateway"\s*\|\s*"deployment_egress_policy"/);
  assert.match(typeSource, /approved_proxy_config_id/);
  assert.match(typeSource, /cli_block_reason/);
  assert.match(apiSource, /getNetworkPolicy:\s*\(\)\s*=>\s*request<DeploymentNetworkPolicy>\("\/api\/settings\/network-policy"\)/);
  assert.doesNotMatch(apiSource, /(?:update|create|delete)NetworkPolicy\s*:/);
});

test("settings renders a compact deployment policy panel with Chinese status and remediation", () => {
  assert.match(settingsSource, /部署网络策略/);
  assert.match(settingsSource, /管理员部署配置/);
  assert.match(settingsSource, /开发模式/);
  assert.match(settingsSource, /内网模式/);
  assert.match(settingsSource, /严格合规模式/);
  assert.match(settingsSource, /模型访问/);
  assert.match(settingsSource, /CLI Agent/);
  assert.match(settingsSource, /企业代理/);
  assert.match(settingsSource, /CA 证书/);
  assert.match(settingsSource, /遥测/);
  assert.match(settingsSource, /远程追踪/);
  assert.match(settingsSource, /Hosted MCP/);
  assert.match(settingsSource, /cli_remediation/);
  assert.match(settingsSource, /最终受部署批准策略约束/);
});

test("settings never renders deployment proxy credentials or a raw proxy endpoint", () => {
  assert.doesNotMatch(settingsSource, /approved_proxy_url/);
  assert.doesNotMatch(settingsSource, /approved_proxy_target/);
  assert.match(settingsSource, /approved_proxy_config_id/);
  assert.match(settingsSource, /approved_no_proxy/);
});

test("agent runtime networking fails closed by default and exposes an explicit offline choice", () => {
  assert.match(typeSource, /requires_network:\s*boolean/);
  assert.match(settingsSource, /requires_network:\s*true/);
  assert.match(settingsSource, /网络访问方式/);
  assert.match(settingsSource, /联网 Agent（需要批准边界）/);
  assert.match(settingsSource, /离线 Agent（OS 网络隔离）/);
  assert.match(settingsSource, /updateAgentRuntimeForm\("requires_network",\s*event\.target\.value === "networked"\)/);
  assert.match(settingsSource, /runtime\.requires_network !== false/);
  assert.match(settingsSource, /htmlFor="agent-runtime-name"/);
  assert.match(settingsSource, /id="agent-runtime-network-access"/);
});

test("raw network deny reasons are not the visible LLM connection result", () => {
  assert.match(settingsSource, /function userFacingLlmTestResult/);
  assert.match(settingsSource, /内网部署策略未批准该模型端点，请联系管理员配置批准的模型服务后重试。/);
  assert.match(settingsSource, /setTestResult\(userFacingLlmTestResult\(result\.message\)\)/);
});
