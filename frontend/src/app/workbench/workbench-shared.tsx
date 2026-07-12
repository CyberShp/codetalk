"use client";

import type { ReactNode } from "react";
import { Download } from "lucide-react";
import { AnimatePresence, motion } from "framer-motion";
import type { AgentCommandResolutionDetail, MaterializeWorkflowOutputsResult, PreparedWorkbenchTaskRun, WorkflowDefinition, WorkflowPreset, WorkbenchWorkflowCapabilities, WorkbenchAcceptanceAudit, WorkbenchTaskArtifact, WorkbenchTaskArtifactContent, WorkbenchTaskRunEvent } from "@/lib/types";

export const MIN_VISIBLE_BUSY_ACTION_MS = 600;
export const WORKFLOW_CANVAS_WIDTH = 1500;
export const WORKFLOW_CANVAS_HEIGHT = 1100;
export const WORKFLOW_NODE_WIDTH = 168;
export const WORKFLOW_NODE_HEIGHT = 96;

export const DEFAULT_WORKFLOW = {
  id: "mr-blackbox-workflow",
  name: "MR 黑盒测试工作流",
  version: 1,
  inputs: [
    {
      id: "mr_link",
      type: "mr_link",
      required: true,
      resolver: "agent_mcp",
      role: "由智能体执行器通过 MCP 凭证读取 MR",
    },
    { id: "design_doc", type: "file", required: false, role: "设计文档" },
    { id: "coverage_report", type: "coverage_report", required: false },
  ],
  steps: [
    {
      id: "agent_collect_mr",
      type: "agent_task",
      provider: "claude-code",
      mcp_profile: "codehub-mcp",
      goal: "读取 MR 差异并产出可校验产物；禁止修改代码。",
      required_artifacts: [
        "mr_snapshot.json",
        "diff.patch",
        "changed_files.json",
      ],
    },
    { id: "validate_evidence", type: "evidence_validate" },
    { id: "render_black_box_cases", type: "report_render" },
  ],
  outputs: [
    { id: "mr_scope", type: "scope_report", from: "validate_evidence" },
    {
      id: "black_box_cases",
      type: "test_cases",
      from: "render_black_box_cases",
      artifact: "black_box_cases.json",
      semantic_import: {
        enabled: true,
        defaults: {
          test_level: "black_box",
          tags: ["mr_blackbox_test"],
        },
      },
    },
  ],
};

export const DEFAULT_INPUTS: Record<string, string> = {};

export type WorkbenchView = "run" | "workflow" | "knowledge" | "diagnostics";

export function workbenchInputsFromSearchParams(
  searchParams: Pick<URLSearchParams, "get">,
): Record<string, string> {
  const inputs: Record<string, string> = {};
  const target =
    searchParams.get("target") ||
    searchParams.get("analysis_object") ||
    searchParams.get("module") ||
    "";
  const outputs = searchParams.get("outputs") || searchParams.get("output_files") || "";
  const mrLink = searchParams.get("mr_link") || searchParams.get("mr") || "";
  const repoPath = searchParams.get("repo_path") || "";
  if (target.trim()) inputs.analysis_object = target.trim();
  if (outputs.trim()) {
    inputs.requested_outputs = outputs.trim();
    inputs.output_files = outputs.trim();
  }
  if (mrLink.trim()) inputs.mr_link = mrLink.trim();
  if (repoPath.trim()) inputs.repo_path = repoPath.trim();
  return inputs;
}

export function workbenchWorkspaceIdFromSearchParams(
  searchParams: Pick<URLSearchParams, "get">,
): string {
  return (
    searchParams.get("workspace_id") ||
    searchParams.get("workspace") ||
    searchParams.get("workspaceId") ||
    ""
  ).trim();
}

export function WorkbenchStageFrame({
  activeWorkbenchView,
  reducedMotion,
  children,
}: {
  activeWorkbenchView: WorkbenchView;
  reducedMotion: boolean;
  children: ReactNode;
}) {
  const className = `ct-workbench-stage grid grid-cols-1 gap-5 ${
    activeWorkbenchView === "knowledge" ? "2xl:grid-cols-2" : ""
  }`;

  if (reducedMotion) {
    return (
      <div key={activeWorkbenchView} className={className}>
        {children}
      </div>
    );
  }

  return (
    <AnimatePresence mode="wait">
      <motion.div
        key={activeWorkbenchView}
        initial={{ opacity: 0, y: 18, scale: 0.985 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, y: -10, scale: 0.99 }}
        transition={{ duration: 0.36, ease: [0.22, 1, 0.36, 1] }}
        className={className}
      >
        {children}
      </motion.div>
    </AnimatePresence>
  );
}

export const CORE_WORKFLOW_PRESET_IDS = new Set([
  "module_analysis",
  "resource_leak_hunt",
  "mr_blackbox_test",
  "patch_impact_review",
  "source_flow_sfmea_blackbox",
]);

export const WORKFLOW_NAME_ZH: Record<string, string> = {
  "MR Black-box Test Workflow": "MR 黑盒测试工作流",
  "MR Black-box Test Design": "MR 黑盒测试工作流",
  "MR Blackbox Test Workflow": "MR 黑盒测试工作流",
  "Testing Activity Orchestration": "测试活动编排工作流",
  "Module Analysis": "模块分析工作流",
  "Resource Leak and Error Branch Hunt": "资源/异常路径排查工作流",
  "Resource Leak Hunt": "资源/异常路径排查工作流",
  "Patch Impact Review": "补丁影响面评审工作流",
  custom_mr_blackbox: "自定义 MR 黑盒测试工作流",
  "mr-blackbox-workflow": "MR 黑盒测试工作流",
  mr_blackbox_test: "MR 黑盒测试工作流",
  testing_activity_orchestration: "测试活动编排工作流",
  module_analysis: "模块分析工作流",
  resource_leak_hunt: "资源/异常路径排查工作流",
  source_flow_sfmea_blackbox: "代码分析-流程-SFMEA-黑盒用例工作流",
  nvmf_connect_io_blackbox: "NVMe-oF 连接/IO 黑盒场景",
  iscsi_login_session_blackbox: "iSCSI 登录/会话黑盒场景",
  bdev_io_reset_blackbox: "bdev IO/reset 黑盒场景",
  rpc_config_negative_blackbox: "RPC/config 负例黑盒场景",
  reactor_thread_poller_blackbox: "reactor/thread/poller 调度黑盒场景",
  nvmf_disconnect_reconnect_blackbox: "NVMe-oF 断连/重连黑盒场景",
  iscsi_auth_failure_blackbox: "iSCSI 认证失败/重置黑盒场景",
  bdev_failover_resource_blackbox: "bdev failover/资源压力黑盒场景",
  blobstore_ftl_recovery_blackbox: "blobstore/FTL 恢复黑盒场景",
  vhost_vfio_user_lifecycle_blackbox: "vhost/vfio-user 生命周期黑盒场景",
  nvmf_tcp_tls_auth_blackbox: "NVMe/TCP TLS/认证黑盒场景",
  bdev_qos_latency_blackbox: "bdev QoS/时延退化黑盒场景",
  jsonrpc_concurrency_idempotency_blackbox: "JSON-RPC 并发/幂等黑盒场景",
  app_startup_shutdown_smoke_blackbox: "应用启动/关闭冒烟黑盒场景",
  nvme_ctrlr_hotplug_reset_blackbox: "NVMe 控制器热插拔/reset 黑盒场景",
  storage_capacity_enospc_recovery_blackbox: "容量/ENOSPC 恢复黑盒场景",
  fault_injection_timeout_recovery_blackbox: "故障注入/超时恢复黑盒场景",
  concurrent_operations_stress_blackbox: "并发操作/压力黑盒场景",
  observability_diagnostics_blackbox: "可观测性/诊断黑盒场景",
  config_compatibility_rollback_blackbox: "配置兼容/回滚黑盒场景",
  lvol_snapshot_clone_blackbox: "lvol 快照/克隆黑盒场景",
  raid_degraded_rebuild_blackbox: "RAID 降级/rebuild 黑盒场景",
  nvme_multipath_failover_blackbox: "NVMe multipath/failover 黑盒场景",
  env_hugepage_memory_blackbox: "环境/hugepage 内存黑盒场景",
  patch_impact: "补丁影响面计划工作流",
  patch_impact_review: "补丁影响面评审工作流",
  nvmf_rdma_transport_blackbox: "NVMe/RDMA transport 黑盒场景",
  iscsi_digest_multi_connection_blackbox: "iSCSI digest/多连接黑盒场景",
  bdev_hotremove_io_error_blackbox: "bdev hotremove/IO 错误黑盒场景",
  blobstore_metadata_powerfail_blackbox: "blobstore 元数据/掉电恢复黑盒场景",
  rpc_security_authz_blackbox: "RPC 安全/权限黑盒场景",
  spdk_cli_rpc_smoke_blackbox: "SPDK CLI/RPC 冒烟黑盒场景",
  target_crash_restart_blackbox: "target 崩溃/重启恢复黑盒场景",
  multi_client_isolation_blackbox: "多客户端隔离黑盒场景",
  queue_depth_backpressure_blackbox: "队列深度/反压黑盒场景",
  io_error_injection_retry_blackbox: "IO 错误注入/重试黑盒场景",
  config_reload_persistence_blackbox: "配置重载/持久化黑盒场景",
  long_running_resource_leak_blackbox: "长跑资源泄漏黑盒场景",
  basic_lifecycle_smoke_blackbox: "基础生命周期冒烟黑盒场景",
  io_stress_performance_blackbox: "IO 压力/性能基线黑盒场景",
  failure_recovery_soak_blackbox: "故障恢复/soak 黑盒场景",
  transport_network_partition_blackbox: "transport 网络分区黑盒场景",
  data_integrity_corruption_blackbox: "数据完整性/损坏黑盒场景",
  upgrade_compatibility_persistence_blackbox: "升级兼容/持久化黑盒场景",
  telemetry_metrics_regression_blackbox: "遥测/指标回归黑盒场景",
  nvmf_subsystem_namespace_acl_blackbox:
    "NVMe-oF subsystem/namespace ACL 黑盒场景",
  iscsi_lun_resize_hotplug_blackbox: "iSCSI LUN resize/hotplug 黑盒场景",
  bdev_crypto_integrity_blackbox: "bdev crypto/完整性黑盒场景",
  scheduler_qos_fairness_blackbox: "scheduler QoS/公平性黑盒场景",
  backup_restore_integrity_blackbox: "备份/恢复完整性黑盒场景",
  nvme_discovery_log_blackbox: "NVMe discovery/log 黑盒场景",
  iscsi_portal_failover_blackbox: "iSCSI portal/failover 黑盒场景",
  bdev_zone_append_blackbox: "bdev zone append 黑盒场景",
  jsonrpc_partial_rollback_blackbox: "JSON-RPC 部分失败/回滚黑盒场景",
  vfio_user_hotplug_reconnect_blackbox: "vfio-user hotplug/reconnect 黑盒场景",
  lvol_thin_snapshot_blackbox: "lvol thin/snapshot 黑盒场景",
  api_contract_negative_blackbox: "API 契约负例黑盒场景",
  state_persistence_restart_blackbox: "状态持久化/重启黑盒场景",
  concurrency_isolation_race_blackbox: "并发隔离/race 黑盒场景",
  performance_capacity_regression_blackbox: "性能容量回归黑盒场景",
  security_access_control_blackbox: "安全访问控制黑盒场景",
};

export function workflowDisplayName(
  workflow: Pick<WorkflowDefinition, "id" | "name"> | string,
): string {
  const id = typeof workflow === "string" ? workflow : workflow.id;
  const name =
    typeof workflow === "string" ? "" : String(workflow.name ?? "").trim();
  const normalizedName = WORKFLOW_NAME_ZH[name] ?? name;
  if (normalizedName && !/[A-Za-z]{4,}/.test(normalizedName))
    return normalizedName;
  return WORKFLOW_NAME_ZH[id] || normalizedName || id;
}

export function workflowPresetGroup(
  preset: WorkflowPreset,
): "核心工作流" | "常用测试场景" {
  if (preset.group === "core") return "核心工作流";
  if (preset.group === "common_test_scenario") return "常用测试场景";
  return CORE_WORKFLOW_PRESET_IDS.has(preset.id)
    ? "核心工作流"
    : "常用测试场景";
}

export const WORKFLOW_BUILDER_SCENARIOS = {
  module_analysis: {
    name: "模块分析",
    inputs:
      "analysis_object:free_text, design_doc:file, coverage_report:coverage_report",
    outputs:
      "source_scope:scope_report=source_scope.json, risk_findings:json, test_cases:test_cases=black_box_cases.json",
    goal: "分析指定模块，校验源码范围，识别风险路径，并生成面向黑盒验证的测试用例。",
    artifacts: "source_scope.json, risk_findings.json, black_box_cases.json",
  },
  resource_leak_hunt: {
    name: "资源/异常路径排查",
    inputs:
      "target_scope:free_text, risk_pattern:free_text, repo_path:directory@local, design_doc:file",
    outputs:
      "risk_findings:json=risk_findings.json, code_evidence:json=evidence_cards.json, test_hooks:json=test_hooks.json, test_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物；除非用户明确不要基于源码，否则读取工作区源码，围绕指定资源、生命周期或异常分支排查泄漏和清理风险，产出证据、测试钩子和可观察测试。",
    artifacts:
      "risk_findings.json, evidence_cards.json, test_hooks.json, black_box_cases.json",
  },
  mr_blackbox_test: {
    name: "MR 黑盒测试",
    inputs:
      "mr_link:mr_link, patch_diff:patch, repo_path:directory@local, design_doc:file, coverage_report:coverage_report",
    outputs:
      "mr_scope:scope_report=mr_snapshot.json, changed_behavior:json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物；使用智能体自持 MCP 凭证或本地 patch 输入读取变更，识别变更行为和影响范围，并生成黑盒测试用例。",
    artifacts:
      "mr_snapshot.json, diff.patch, changed_files.json, black_box_cases.json",
  },
  testing_activity_orchestration: {
    name: "测试活动编排",
    inputs:
      "test_goal:free_text, repo_path:directory@local, requirements:file, coverage_report:coverage_report, defect_report:file, environment_notes:long_text",
    outputs:
      "test_strategy:markdown=test_strategy.md, test_plan:json=test_plan.json, execution_matrix:json=execution_matrix.json, coverage_gap_report:json=coverage_gap_report.json, defect_triage:markdown=defect_triage.md, release_readiness:markdown=release_readiness.md",
    goal: "优先检查工作区源码、GitNexus、CGC、输入文件和历史证据；围绕测试目标组织完整测试活动，覆盖测试策略、范围与风险、环境准备、测试设计、执行矩阵、覆盖率缺口、缺陷分诊、回归范围、性能/可靠性活动、验收准入准出和可下载报告。",
    artifacts:
      "test_strategy.md, test_plan.json, execution_matrix.json, coverage_gap_report.json, defect_triage.md, release_readiness.md",
    skills: [
      "source-evidence-first",
      "test-strategy-planning",
      "coverage-gap-analysis",
      "test-execution-orchestration",
      "defect-triage-regression",
      "performance-reliability-testing",
      "artifact-contract",
    ],
  },
  patch_impact_review: {
    name: "补丁影响面评审",
    inputs:
      "patch_diff:patch, patch_plan:file, repo_path:directory@local, design_doc:file, analysis_object:free_text",
    outputs:
      "impact_scope:scope_report=impact_scope.json, before_after_flow:markdown=flow_delta.md, test_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物；读取补丁方案或 diff，对比变更前后流程，校验影响范围、兼容性风险和测试范围，并生成测试建议。",
    artifacts:
      "impact_scope.json, flow_delta.md, test_recommendations.json, black_box_cases.json",
  },
  source_flow_sfmea_blackbox: {
    name: "代码分析-流程-SFMEA-黑盒用例",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, design_doc:file, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物；除非用户明确不要基于源码，否则读取工作区源码，产出代码证据、流程梳理、SFMEA 和外部可执行黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  nvmf_connect_io_blackbox: {
    name: "NVMe-oF connect/IO",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 lib/nvmf 与 test/nvmf 的 connect、认证、queue 建立、IO 提交、timeout、disconnect/reconnect、controller reset，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  iscsi_login_session_blackbox: {
    name: "iSCSI login/session",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 lib/iscsi 与 test/iscsi_tgt 的 login、CHAP、digest、多连接、认证失败、重定向、session reset 和 initiator 断开，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  bdev_io_reset_blackbox: {
    name: "bdev IO/reset/failover",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 lib/bdev、module/bdev 与 test/bdev 的 open、submit、complete、错误返回、pending reset、I/O drain、reconnect、failover 和资源压力，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  rpc_config_negative_blackbox: {
    name: "RPC/config 负例",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 RPC/config 的非法参数、重复调用、顺序错误、部分成功回滚、幂等性和诊断信号，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  reactor_thread_poller_blackbox: {
    name: "reactor/thread/poller",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 lib/thread、lib/event 与 scheduler/poller 相关代码的跨线程消息、poller 阻塞、长任务调度、并发恢复和性能退化，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  nvmf_disconnect_reconnect_blackbox: {
    name: "NVMe-oF 断连/重连",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 lib/nvmf 与 test/nvmf 的 keep-alive timeout、disconnect、reconnect、controller reset、qpair teardown、transport error 和恢复路径，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  iscsi_auth_failure_blackbox: {
    name: "iSCSI 认证失败/重置",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 lib/iscsi 与 test/iscsi_tgt 的 CHAP/authentication failure、digest mismatch、redirect、session reset、logout、initiator disconnect 和恢复诊断，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  bdev_failover_resource_blackbox: {
    name: "bdev failover/资源压力",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 lib/bdev、module/bdev 与 test/bdev 的 failover、reconnect、resource exhaustion、no-memory、I/O drain、reset ordering 和公开错误报告，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  blobstore_ftl_recovery_blackbox: {
    name: "blobstore/FTL 恢复",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 lib/blob、lib/ftl、module/bdev/ftl 与测试目录的 metadata recovery、ENOSPC、异常关闭、super block 一致性、relocation 和 restart recovery，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  vhost_vfio_user_lifecycle_blackbox: {
    name: "vhost/vfio-user 生命周期",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 lib/vhost、lib/vfio_user 与测试目录的 device lifecycle、queue 配置、guest attach/detach、socket cleanup、reset 和错误恢复，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  nvmf_tcp_tls_auth_blackbox: {
    name: "NVMe/TCP TLS/认证",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 NVMe/TCP TLS 与认证配置、证书/密钥不匹配、安全连接协商、fallback 拒绝、重连和诊断信号，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  bdev_qos_latency_blackbox: {
    name: "bdev QoS/时延退化",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 bdev QoS、限速、队列深度压力、时延尖刺、超时报告、公平性和持续 IO 压力下恢复，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  jsonrpc_concurrency_idempotency_blackbox: {
    name: "JSON-RPC 并发/幂等",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 JSON-RPC 并发调用、重复 create/delete、幂等性、部分成功、顺序竞争、回滚和外部可观测错误，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  app_startup_shutdown_smoke_blackbox: {
    name: "应用启动/关闭冒烟",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 app、lib/event、scripts/rpc.py 与 test/app/test/json_config 的应用启动、配置加载、RPC ready、signal、graceful shutdown、restart 和诊断信号，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  nvme_ctrlr_hotplug_reset_blackbox: {
    name: "NVMe 控制器热插拔/reset",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 lib/nvme 与 test/nvme 的 controller attach、identify、reset、timeout、hotremove、namespace change、reconnect 和公开错误报告，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  storage_capacity_enospc_recovery_blackbox: {
    name: "容量/ENOSPC 恢复",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 lib/bdev、lib/blob、lib/ftl 与相关测试的 capacity pressure、ENOSPC、allocation failure、metadata persistence、partial write、retry、cleanup 和 recovery，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  nvmf_rdma_transport_blackbox: {
    name: "NVMe/RDMA transport",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 NVMe/RDMA transport 的连接建立、queue pair、RDMA CM 事件、内存注册、disconnect、retry、错误恢复和公开诊断，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  iscsi_digest_multi_connection_blackbox: {
    name: "iSCSI digest/多连接",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 iSCSI header/data digest、多连接 session、连接漂移、校验失败、恢复和外部日志/状态，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  bdev_hotremove_io_error_blackbox: {
    name: "bdev hotremove/IO 错误",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 bdev hotremove、底层设备丢失、IO 错误上报、reset、drain、重试和可观测状态变化，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  blobstore_metadata_powerfail_blackbox: {
    name: "blobstore 元数据/掉电恢复",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 blobstore 元数据更新、异常关闭、掉电重启、super block/cluster 一致性、部分写入和恢复验证，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  rpc_security_authz_blackbox: {
    name: "RPC 安全/权限",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 RPC 暴露面、认证/授权边界、非法命令、敏感参数、失败审计、重放和用户可见错误，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  fault_injection_timeout_recovery_blackbox: {
    name: "故障注入/超时恢复",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析故障注入、transport error、timeout、retry、cleanup、进程重启和恢复诊断，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  concurrent_operations_stress_blackbox: {
    name: "并发操作/压力",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析并发 create/delete、connect/disconnect、运行中 IO、队列压力、幂等性、顺序竞争和压力退化，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  observability_diagnostics_blackbox: {
    name: "可观测性/诊断",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析日志、计数器、公开状态命令、诊断产物、告警路径和失败定位信号，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  config_compatibility_rollback_blackbox: {
    name: "配置兼容/回滚",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析配置兼容、非法/混合版本输入、部分应用、回滚、重启持久化、幂等性和用户可见诊断，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  lvol_snapshot_clone_blackbox: {
    name: "lvol 快照/克隆",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 module/bdev/lvol、lib/blob 与测试目录的 lvol create/delete、snapshot、clone、resize、thin provision、metadata persistence、ENOSPC 和恢复行为，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  raid_degraded_rebuild_blackbox: {
    name: "RAID 降级/rebuild",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 module/bdev/raid 与测试目录的 RAID create/start/stop、member failure、degraded mode、rebuild、I/O continuity、resync progress 和外部诊断，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  nvme_multipath_failover_blackbox: {
    name: "NVMe multipath/failover",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 lib/nvme、module/bdev/nvme 与测试目录的 multipath attach、path loss、ANA state、failover、reconnect、I/O continuity、timeout 和公开状态信号，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  env_hugepage_memory_blackbox: {
    name: "环境/hugepage 内存",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 env 初始化、hugepage 分配、memory pressure、非法启动参数、cleanup、restart 和可观测诊断，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  spdk_cli_rpc_smoke_blackbox: {
    name: "SPDK CLI/RPC 冒烟",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 scripts/rpc.py、spdkcli、test/json_config 与 app 启动路径的 RPC ready、create/list/delete、非法命令和诊断输出，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  target_crash_restart_blackbox: {
    name: "target 崩溃/重启恢复",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 target 进程崩溃、signal 终止、重启 readiness、客户端重连、状态清理、运行中 IO 可观测性和操作者诊断，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  multi_client_isolation_blackbox: {
    name: "多客户端隔离",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析多 initiator/多客户端隔离、namespace 可见性、访问边界、共享资源压力、跨 session 泄漏症状和公开诊断，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  queue_depth_backpressure_blackbox: {
    name: "队列深度/反压",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 queue depth 限制、outstanding IO 饱和、反压、时延尖刺、超时报告、限流和压力解除后的恢复，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  io_error_injection_retry_blackbox: {
    name: "IO 错误注入/重试",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析外部可触发的 IO 错误注入、retry、部分完成、transport failure、fail-fast 行为和错误后的数据路径恢复，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  config_reload_persistence_blackbox: {
    name: "配置重载/持久化",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 config reload、保存配置持久化、重启恢复、部分应用、回滚、重复命令和外部状态校验，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  long_running_resource_leak_blackbox: {
    name: "长跑资源泄漏",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析长时间 create/delete、connect/disconnect、持续 IO、资源增长、清理、指标、日志和 soak 测试失败诊断，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  basic_lifecycle_smoke_blackbox: {
    name: "基础生命周期冒烟",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 create/list/update/delete、启动 readiness、重启恢复、清理和诊断输出，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  io_stress_performance_blackbox: {
    name: "IO 压力/性能基线",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析持续 IO、混合读写、queue depth 压力、时延/吞吐基线、性能退化和可观测指标，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  failure_recovery_soak_blackbox: {
    name: "故障恢复/soak",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析长时间运行、重启、断连/重连、资源压力、清理、恢复和操作者可见证据，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  transport_network_partition_blackbox: {
    name: "transport 网络分区",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 NVMe-oF/iSCSI transport 的 packet loss、network partition、reconnect、timeout、keep-alive、IO continuity 和恢复诊断，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  data_integrity_corruption_blackbox: {
    name: "数据完整性/损坏",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析数据完整性、checksum/digest mismatch、partial write、read-after-write、metadata corruption 和恢复信号，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  upgrade_compatibility_persistence_blackbox: {
    name: "升级兼容/持久化",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 upgrade/downgrade、restart persistence、saved config compatibility、metadata version、rollback 和迁移诊断，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  telemetry_metrics_regression_blackbox: {
    name: "遥测/指标回归",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 telemetry、counters、logs、status commands、metric regression、alertability 和 failure triage 信号，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  nvmf_subsystem_namespace_acl_blackbox: {
    name: "NVMe-oF subsystem/namespace ACL",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 subsystem/namespace 生命周期、host allow-list、ANA 可见性、namespace attach/detach、reconnect 和 access denied 诊断，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  iscsi_lun_resize_hotplug_blackbox: {
    name: "iSCSI LUN resize/hotplug",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 iSCSI target LUN add/remove、resize、hotplug visibility、initiator rescan、active IO、session recovery 和诊断输出，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  bdev_crypto_integrity_blackbox: {
    name: "bdev crypto/完整性",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 crypto/integrity bdev 配置、key mismatch、data verification、非法参数、失败上报、性能影响和恢复路径，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  scheduler_qos_fairness_blackbox: {
    name: "scheduler QoS/公平性",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 scheduler/poller/reactor、queue depth、QoS、公平性、starvation、latency regression 和竞争负载恢复，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  backup_restore_integrity_blackbox: {
    name: "备份/恢复完整性",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 export/import、save/restore、备份式快照、checksum validation、partial restore、corrupted input、restart persistence 和诊断输出，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  nvme_discovery_log_blackbox: {
    name: "NVMe discovery/log",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 discovery log page、identify/controller data、log retrieval、subsystem 可见性变化、非法请求、transport loss 和诊断输出，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  iscsi_portal_failover_blackbox: {
    name: "iSCSI portal/failover",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 portal group 变更、target discovery、failover、reconnect、stale session、network partition 和恢复诊断输出，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  bdev_zone_append_blackbox: {
    name: "bdev zone append",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 zoned bdev write pointer、zone append、reset/open/finish、边界错误、并发写、容量压力和 completion 行为，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  jsonrpc_partial_rollback_blackbox: {
    name: "JSON-RPC 部分失败/回滚",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析多步骤 RPC、重复调用、部分成功、rollback/cleanup、幂等性、顺序错误和客户端可见错误 payload，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  vfio_user_hotplug_reconnect_blackbox: {
    name: "vfio-user hotplug/reconnect",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 vfio-user device hotplug、guest detach、reconnect、queue reconfiguration、socket loss、生命周期恢复和状态转移，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
  lvol_thin_snapshot_blackbox: {
    name: "lvol thin/snapshot",
    inputs:
      "analysis_object:free_text, repo_path:directory@local, coverage_report:coverage_report",
    outputs:
      "source_scope:json=source_scope.json, code_evidence:json=evidence_cards.json, flow_map:markdown=flow_map.md, sfmea:json=sfmea.json, black_box_cases:test_cases=black_box_cases.json",
    goal: "优先检查 GitNexus 和 CGC 产物，然后分析 thin provisioning、snapshot/clone 生命周期、ENOSPC、删除顺序、metadata persistence、restart recovery 和完整性观测点，输出代码证据、流程、SFMEA 和黑盒测试用例。",
    artifacts:
      "source_scope.json, evidence_cards.json, flow_map.md, sfmea.json, black_box_cases.json",
  },
} as const;

export const DEFAULT_BUILDER_OUTPUT_SCHEMAS = {
  source_scope: {
    type: "object",
    required: ["scope_id", "query", "files"],
    properties: {
      scope_id: { type: "string" },
      query: { type: "string" },
      files: { type: "array" },
      entry_points: { type: "array" },
    },
  },
  risk_findings: {
    type: "array",
    items: { type: "object" },
  },
  mr_scope: {
    type: "object",
    required: ["kind", "source", "status", "summary"],
    properties: {
      kind: { type: "string" },
      source: { type: "string" },
      status: { type: "string" },
      summary: { type: "string" },
      changed_files: { type: "array" },
    },
  },
  impact_scope: {
    type: "array",
    items: { type: "object" },
  },
  issue_candidates: {
    type: "array",
    items: { type: "object" },
  },
  repro_paths: {
    type: "array",
    items: { type: "object" },
  },
  test_hooks: {
    type: "array",
    items: { type: "object" },
  },
  code_evidence: {
    type: "array",
    items: { type: "object" },
  },
  changed_behavior: {
    type: "object",
    required: ["summary"],
    properties: {
      summary: { type: "string" },
      affected_files: { type: "array" },
    },
  },
  sfmea: {
    type: "array",
    items: { type: "object" },
  },
  black_box_cases: {
    type: "object",
    required: ["cases"],
    properties: {
      cases: { type: "array" },
    },
  },
  test_plan: {
    type: "object",
    required: ["scope", "risks", "activities", "entry_criteria", "exit_criteria"],
    properties: {
      scope: { type: "array" },
      risks: { type: "array" },
      activities: { type: "array" },
      entry_criteria: { type: "array" },
      exit_criteria: { type: "array" },
    },
  },
  execution_matrix: {
    type: "object",
    required: ["batches"],
    properties: {
      batches: { type: "array" },
      environments: { type: "array" },
      observability: { type: "array" },
      rerun_policy: { type: "array" },
    },
  },
  coverage_gap_report: {
    type: "object",
    required: ["gaps", "recommendations"],
    properties: {
      gaps: { type: "array" },
      recommendations: { type: "array" },
      source_evidence: { type: "array" },
    },
  },
};

export const DEFAULT_BUILDER_EVIDENCE_MAPPINGS = {
  risk_findings: {
    enabled: true,
    kind: "resource_risk_finding",
    subject_key_field: "finding_id",
    path_field: "file_path",
    symbol_field: "function",
    status: "candidate_output",
    text_fields: ["summary", "risk", "resource", "function"],
  },
  issue_candidates: {
    enabled: true,
    kind: "issue_candidate",
    subject_key_field: "issue_id",
    path_field: "file_path",
    symbol_field: "function",
    status: "candidate_output",
    text_fields: ["summary", "issue_type", "trigger", "function"],
  },
  changed_behavior: {
    enabled: true,
    kind: "changed_behavior",
    subject_key_field: "behavior_id",
    path_field: "file_path",
    symbol_field: "symbol",
    status: "candidate_output",
    text_fields: ["summary", "before", "after", "test_scope"],
  },
  impact_scope: {
    enabled: true,
    kind: "patch_impact_scope",
    subject_key_field: "impact_id",
    path_field: "file_path",
    symbol_field: "symbol",
    status: "candidate_output",
    text_fields: ["summary", "flow_delta", "impact", "risk", "test_scope"],
  },
};

export const DEFAULT_BUILDER_SEMANTIC_IMPORTS = {
  black_box_cases: {
    enabled: true,
    defaults: {
      test_level: "black_box",
      reuse_rule: "terminology_only_not_source_truth",
    },
  },
  test_cases: {
    enabled: true,
    defaults: {
      test_level: "black_box",
      reuse_rule: "terminology_only_not_source_truth",
    },
  },
};

export const DEFAULT_BUILDER_INPUT_SCHEMAS = {
  patch_file: {
    type: "object",
    required: ["path"],
    properties: {
      path: { type: "string", minLength: 1 },
    },
  },
  patch_diff: {
    type: "object",
    required: ["path"],
    properties: {
      path: { type: "string", minLength: 1 },
    },
  },
  design_doc: {
    type: "object",
    required: ["path"],
    properties: {
      path: { type: "string", minLength: 1 },
    },
  },
  coverage_report: {
    type: "object",
    required: ["path"],
    properties: {
      path: { type: "string", minLength: 1 },
    },
  },
};

export const DEFAULT_SEMANTIC_CASE = {
  case_id: "nvme_tcp_tls_handshake_fail",
  feature: "NVMe TCP TLS",
  module: "nvmf_tcp",
  test_level: "black_box",
  scenario: "TLS handshake fails and connection is released",
  terms: ["TLS negotiation", "queue pair", "connection release"],
  tags: ["resource_cleanup", "exception_branch"],
  preconditions: ["Target configured with TLS enabled"],
  actions: [
    "Create an NVMe TCP connection with invalid TLS credentials",
    "Observe connection setup failure",
  ],
  expected: [
    "The session is rejected",
    "All allocated connection resources are released",
  ],
  assertion_style:
    "Prefer observable status, logs, counters, and connection lifecycle checks",
};

export const DEFAULT_SEMANTIC_LINES = [
  "TLS handshake fails with invalid credentials -> connection is rejected and resources are released",
  "TLS disabled by configuration -> connection uses the non-TLS path and reports the selected mode",
].join("\n");

export function pretty(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

export function parseJsonObject(value: string): Record<string, unknown> {
  const parsed = JSON.parse(value) as unknown;
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("JSON must be an object");
  }
  return parsed as Record<string, unknown>;
}

export function workflowIdFromJson(value: string): string {
  try {
    return String(parseJsonObject(value).id ?? "").trim();
  } catch {
    return "";
  }
}

export function parseJsonValue(value: string): unknown {
  return JSON.parse(value) as unknown;
}

export function parseCommaSeparated(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

export function uniqueWorkflowStrings(values: string[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  values.forEach((value) => {
    const text = value.trim();
    if (!text || seen.has(text)) return;
    seen.add(text);
    result.push(text);
  });
  return result;
}

export function parseWorkflowSpecList(
  value: string,
  defaultType: string,
): Array<{
  id: string;
  type: string;
  resolver?: string;
  artifact?: string;
}> {
  return parseCommaSeparated(value).map((item) => {
    const [specPart, artifactPart] = item.split("=").map((part) => part.trim());
    const [typedPart, resolverPart] = specPart
      .split("@")
      .map((part) => part.trim());
    const [id, type] = typedPart.split(":").map((part) => part.trim());
    if (!id) {
      throw new Error("Workflow builder entries must use id:type");
    }
    return {
      id,
      type: type || defaultType,
      ...(resolverPart ? { resolver: resolverPart } : {}),
      ...(artifactPart ? { artifact: artifactPart } : {}),
    };
  });
}

export const WORKFLOW_MODULE_PALETTE = [
  {
    id: "input",
    label: "输入模块",
    tone: "border-outline-variant/35 bg-surface text-on-surface",
  },
  {
    id: "agent",
    label: "智能体模块",
    tone: "border-outline-variant/35 bg-surface text-on-surface",
  },
  {
    id: "mcp",
    label: "MCP 模块",
    tone: "border-outline-variant/35 bg-surface text-on-surface",
  },
  {
    id: "skills",
    label: "Skills 模块",
    tone: "border-outline-variant/35 bg-surface text-on-surface",
  },
  {
    id: "gitnexus",
    label: "GitNexus 模块",
    tone: "border-outline-variant/35 bg-surface text-on-surface",
  },
  {
    id: "cgc",
    label: "CGC 模块",
    tone: "border-outline-variant/35 bg-surface text-on-surface",
  },
  {
    id: "output",
    label: "输出模块",
    tone: "border-outline-variant/35 bg-surface text-on-surface",
  },
];

export type WorkflowPaletteModuleId = (typeof WORKFLOW_MODULE_PALETTE)[number]["id"];
export type WorkflowCanvasNodeKind = "input" | "context" | "agent" | "output" | "verify";
export type WorkflowCanvasNode = {
  id: string;
  kind: WorkflowCanvasNodeKind;
  title: string;
  subtitle: string;
  body: string[];
  x: number;
  y: number;
  source: "contract" | "canvas";
  config?: Record<string, unknown>;
};
export type WorkflowCanvasEdge = {
  id: string;
  source: string;
  target: string;
  label?: string;
};
export type WorkflowDraftEdge = {
  sourceId: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
};
export type WorkflowNodePosition = { x: number; y: number };
export type WorkflowCanvasLayout = {
  nodes: Array<{
    id: string;
    kind: WorkflowCanvasNodeKind;
    title: string;
    subtitle: string;
    x: number;
    y: number;
    source: "contract" | "canvas";
    config?: Record<string, unknown>;
  }>;
  edges?: WorkflowCanvasEdge[];
  hidden_edge_ids?: string[];
  hidden_node_ids: string[];
};

export const WORKFLOW_NODE_TONE: Record<WorkflowCanvasNodeKind, string> = {
  input: "border-outline-variant/35 bg-surface",
  context: "border-outline-variant/35 bg-surface",
  agent: "border-outline-variant/35 bg-surface",
  output: "border-outline-variant/35 bg-surface",
  verify: "border-outline-variant/35 bg-surface",
};

export const WORKFLOW_NODE_ACCENT: Record<WorkflowCanvasNodeKind, string> = {
  input: "bg-cyan-600",
  context: "bg-blue-600",
  agent: "bg-indigo-600",
  output: "bg-slate-500",
  verify: "bg-amber-700",
};

export type WorkflowSkillOption = NonNullable<
  WorkbenchWorkflowCapabilities["skill_catalog"]
>[number];

export const FALLBACK_WORKFLOW_SKILLS: WorkflowSkillOption[] = [
  {
    id: "source-evidence-first",
    label: "源码证据优先",
    source: "codetalk_builtin",
    default_enabled: true,
    description: "先查工作区源码、GitNexus 和 CGC，再生成结论。",
    prompt_hint: "优先读取工作区源码、GitNexus 和 CGC 产物；关键结论必须引用真实文件或产物证据。",
  },
  {
    id: "storage-flow-analysis",
    label: "存储流程梳理",
    source: "codetalk_builtin",
    default_enabled: true,
    description: "梳理入口、状态、异常分支、恢复路径和外部行为。",
    prompt_hint: "按入口、前置条件、关键状态、正常流程、异常流程、恢复路径和外部可观测行为组织分析。",
  },
  {
    id: "sfmea",
    label: "SFMEA",
    source: "codetalk_builtin",
    default_enabled: true,
    description: "生成结构化 failure mode、评分和 mitigation。",
    prompt_hint: "SFMEA 每条必须包含 failure mode、cause、effect、detection、severity、occurrence、detection score、RPN、mitigation，并解释评分依据。",
  },
  {
    id: "black-box-test-design",
    label: "黑盒测试设计",
    source: "codetalk_builtin",
    default_enabled: true,
    description: "只描述外部输入、操作、预期结果和观测点。",
    prompt_hint: "黑盒用例不得要求修改内部代码或调用内部函数；每条包含前置条件、步骤、预期、观测点和失败诊断线索。",
  },
  {
    id: "test-strategy-planning",
    label: "测试策略与计划",
    source: "codetalk_builtin",
    default_enabled: false,
    description: "拆解范围、风险、资源、环境、准入/准出和里程碑。",
    prompt_hint: "输出测试策略、范围、风险优先级、准入/准出标准、资源/环境依赖、里程碑和未决问题。",
  },
  {
    id: "coverage-gap-analysis",
    label: "覆盖率与缺口分析",
    source: "codetalk_builtin",
    default_enabled: false,
    description: "分析覆盖率、低覆盖入口、灰盒/黑盒边界和补充建议。",
    prompt_hint: "结合覆盖率文件、源码入口和现有测试目录，标出覆盖缺口、补充测试建议和证据映射。",
  },
  {
    id: "test-execution-orchestration",
    label: "测试执行编排",
    source: "codetalk_builtin",
    default_enabled: false,
    description: "生成执行矩阵、批次、环境、观测点、失败处置和复跑策略。",
    prompt_hint: "输出可执行测试矩阵，包含环境、前置条件、批次顺序、并发/长跑安排、观测指标、失败诊断和复跑规则。",
  },
  {
    id: "defect-triage-regression",
    label: "缺陷分诊与回归",
    source: "codetalk_builtin",
    default_enabled: false,
    description: "基于失败、日志、patch、风险和历史证据判断分级与回归范围。",
    prompt_hint: "输出缺陷分级、复现线索、影响范围、回归测试范围、阻塞/放行建议和需要补充的证据。",
  },
  {
    id: "performance-reliability-testing",
    label: "性能与可靠性测试",
    source: "codetalk_builtin",
    default_enabled: false,
    description: "覆盖性能基线、压力、soak、故障恢复、资源泄漏和指标。",
    prompt_hint: "输出性能/可靠性测试计划，包含基线、负载模型、时延/吞吐/资源指标、故障注入、soak、退化阈值和诊断数据。",
  },
  {
    id: "artifact-contract",
    label: "产物契约",
    source: "codetalk_builtin",
    default_enabled: true,
    description: "要求 Agent 写入声明的可下载 artifact。",
    prompt_hint: "必须把结果写入 required_artifacts 声明的文件；终端文字只能作为进度说明，不能替代 artifact。",
  },
];

export const DEFAULT_BUILDER_SKILL_IDS = FALLBACK_WORKFLOW_SKILLS.filter(
  (skill) => skill.default_enabled,
).map((skill) => skill.id);

export function workflowPaletteKind(moduleId: WorkflowPaletteModuleId): WorkflowCanvasNodeKind {
  if (moduleId === "input" || moduleId === "agent" || moduleId === "output") {
    return moduleId;
  }
  return "context";
}

export function workflowPaletteSubtitle(moduleId: WorkflowPaletteModuleId): string {
  switch (moduleId) {
    case "input":
      return "输入契约";
    case "agent":
      return "执行步骤";
    case "mcp":
      return "工具配置";
    case "skills":
      return "技能上下文";
    case "gitnexus":
      return "源码证据";
    case "cgc":
      return "调用图证据";
    case "output":
      return "输出契约";
    default:
      return "画布模块";
  }
}

export function clampWorkflowNodePosition(
  position: WorkflowNodePosition,
): WorkflowNodePosition {
  return {
    x: Math.max(
      0,
      Math.min(position.x, WORKFLOW_CANVAS_WIDTH - WORKFLOW_NODE_WIDTH),
    ),
    y: Math.max(
      0,
      Math.min(position.y, WORKFLOW_CANVAS_HEIGHT - WORKFLOW_NODE_HEIGHT),
    ),
  };
}

export function isWorkflowCanvasNodeKind(value: unknown): value is WorkflowCanvasNodeKind {
  return (
    value === "input" ||
    value === "context" ||
    value === "agent" ||
    value === "output" ||
    value === "verify"
  );
}

export function workflowLayoutFromPayload(payload: unknown): WorkflowCanvasLayout | null {
  if (!payload || typeof payload !== "object") return null;
  const ui = (payload as { ui?: unknown }).ui;
  if (!ui || typeof ui !== "object") return null;
  const layout = (ui as { layout?: unknown }).layout;
  if (!layout || typeof layout !== "object") return null;
  const rawNodes = (layout as { nodes?: unknown }).nodes;
  const rawEdges = (layout as { edges?: unknown }).edges;
  const rawHidden = (layout as { hidden_node_ids?: unknown }).hidden_node_ids;
  const rawHiddenEdges = (layout as { hidden_edge_ids?: unknown }).hidden_edge_ids;
  const nodes = Array.isArray(rawNodes)
    ? rawNodes
        .map<WorkflowCanvasLayout["nodes"][number] | null>((item) => {
          if (!item || typeof item !== "object") return null;
          const record = item as Record<string, unknown>;
          const id = String(record.id ?? "").trim();
          const kind = record.kind;
          if (!id || !isWorkflowCanvasNodeKind(kind)) return null;
          return {
            id,
            kind,
            title: String(record.title ?? id),
            subtitle: String(record.subtitle ?? ""),
            x: Number.isFinite(Number(record.x)) ? Number(record.x) : 0,
            y: Number.isFinite(Number(record.y)) ? Number(record.y) : 0,
            source: record.source === "canvas" ? "canvas" : "contract",
            config:
              record.config &&
              typeof record.config === "object" &&
              !Array.isArray(record.config)
                ? (record.config as Record<string, unknown>)
                : undefined,
          };
        })
        .filter(
          (item): item is WorkflowCanvasLayout["nodes"][number] => item !== null,
        )
    : [];
  const hidden_node_ids = Array.isArray(rawHidden)
    ? rawHidden.map((item) => String(item)).filter(Boolean)
    : [];
  const hidden_edge_ids = Array.isArray(rawHiddenEdges)
    ? rawHiddenEdges.map((item) => String(item)).filter(Boolean)
    : [];
  const edges = Array.isArray(rawEdges)
    ? rawEdges
        .map((item) => {
          if (!item || typeof item !== "object") return null;
          const record = item as Record<string, unknown>;
          const source = String(record.source ?? "").trim();
          const target = String(record.target ?? "").trim();
          if (!source || !target || source === target) return null;
          const label = String(record.label ?? "").trim();
          return {
            id: String(record.id ?? `${source}->${target}`).trim(),
            source,
            target,
            ...(label ? { label } : {}),
          };
        })
        .filter((item): item is WorkflowCanvasEdge => Boolean(item))
    : [];
  return { nodes, edges, hidden_node_ids, hidden_edge_ids };
}

export function safeWorkflowSpecList(
  value: string,
  defaultType: string,
): Array<{ id: string; type: string; resolver?: string; artifact?: string }> {
  try {
    return parseWorkflowSpecList(value, defaultType);
  } catch {
    return [];
  }
}

export function workflowSpecToText(spec: {
  id: string;
  type: string;
  resolver?: string;
  artifact?: string;
}): string {
  const base = `${spec.id}:${spec.type}${spec.resolver ? "@" + spec.resolver : ""}`;
  return spec.artifact ? `${base}=${spec.artifact}` : base;
}

export function workflowItemLabel(
  labels: Record<string, string>,
  id: string,
): string {
  return (labels[id] || id).trim();
}

export function workflowInputDisplayName(input: Record<string, unknown>): string {
  return String(input.label ?? input.role ?? input.id ?? "输入");
}

export function safeArtifactDownloadFilename(relativePath: string): string {
  const filename = relativePath
    .split("/")
    .filter(Boolean)
    .join("__")
    .replace(/[\\/:*?"<>|]+/g, "-")
    .slice(0, 120);
  return filename || "workbench-artifact.txt";
}

export function downloadTextFile(filename: string, content: string, type: string) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.rel = "noopener";
  document.body.appendChild(link);
  window.setTimeout(() => {
    link.click();
    window.setTimeout(() => {
      link.remove();
      URL.revokeObjectURL(url);
    }, 30_000);
  }, 0);
}

export function ArtifactPreviewCard({
  artifactContent,
  fullDownloadHref,
}: {
  artifactContent: WorkbenchTaskArtifactContent;
  fullDownloadHref?: string;
}) {
  const safePreviewText = artifactContent.content_redacted
    ? "产物内容已脱敏，内联预览已隐藏。"
    : artifactContent.content;
  return (
    <div className="rounded-lg border border-outline-variant/30 bg-surface p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-[11px] font-semibold text-on-surface">
            当前预览
          </p>
          <p className="mt-0.5 break-all font-data text-[11px] text-on-surface">
            {artifactContent.relative_path}
          </p>
          <div className="mt-1 flex flex-wrap gap-1.5 text-[10px] text-on-surface-variant">
            <span className="rounded bg-surface-container px-1.5 py-0.5 font-data">
              {artifactContent.kind}
            </span>
            <span className="rounded bg-surface-container px-1.5 py-0.5 font-data">
              sha:{artifactContent.sha256.slice(0, 12)}
            </span>
            {artifactContent.truncated && (
              <span className="rounded bg-warning-container px-1.5 py-0.5 text-on-warning-container">
                已截断
              </span>
            )}
            {artifactContent.content_redacted && (
              <span className="rounded bg-warning-container px-1.5 py-0.5 text-on-warning-container">
                已脱敏
              </span>
            )}
          </div>
        </div>
        {artifactContent.is_text && fullDownloadHref ? (
          <a
            title={
              artifactContent.content_redacted
                ? "下载完整脱敏产物"
                : "下载完整产物"
            }
            href={fullDownloadHref}
            download={safeArtifactDownloadFilename(artifactContent.relative_path)}
            className="inline-flex items-center gap-1 rounded-md bg-surface-container px-2 py-1 text-[11px] font-medium text-on-surface transition-colors hover:bg-surface-container-high"
          >
            <Download size={13} />
            {artifactContent.content_redacted
              ? "下载完整脱敏产物"
              : "下载完整产物"}
          </a>
        ) : artifactContent.is_text ? (
          <button
            type="button"
            title={
              artifactContent.content_redacted
                ? "下载当前脱敏后的预览内容"
                : "下载当前预览内容"
            }
            onClick={() =>
              downloadTextFile(
                safeArtifactDownloadFilename(artifactContent.relative_path),
                safePreviewText,
                "text/plain;charset=utf-8",
              )
            }
            className="inline-flex items-center gap-1 rounded-md bg-surface-container px-2 py-1 text-[11px] font-medium text-on-surface transition-colors hover:bg-surface-container-high"
          >
            <Download size={13} />
            {artifactContent.content_redacted ? "下载脱敏预览" : "下载预览"}
          </button>
        ) : null}
      </div>
      {artifactContent.content_redacted ? (
        <p className="mt-2 rounded-md bg-warning-container/60 px-2 py-2 text-[11px] text-on-warning-container">
          产物内容已脱敏，内联预览已隐藏。
        </p>
      ) : artifactContent.is_text ? (
        <pre className="mt-2 max-h-72 overflow-auto rounded-md bg-surface-container p-2 font-data text-[10px] leading-relaxed text-on-surface-variant">
          {safePreviewText || "此产物暂无可预览文本。"}
        </pre>
      ) : (
        <p className="mt-2 rounded-md bg-surface-container px-2 py-2 text-[11px] text-on-surface-variant">
          此产物不是文本文件，可在完整产物包中下载查看。
        </p>
      )}
    </div>
  );
}

export function outputArtifactForSpec(
  outputId: string,
  outputType: string,
  artifacts: string[],
): string {
  const normalizedOutput = outputId.replace(/[-_\s]/g, "").toLowerCase();
  const matchingArtifact = artifacts.find((artifact) => {
    const stem = artifact.replace(/^.*[\\/]/, "").replace(/\.[^.]+$/, "");
    const normalizedStem = stem.replace(/[-_\s]/g, "").toLowerCase();
    return (
      normalizedStem === normalizedOutput ||
      normalizedStem.includes(normalizedOutput)
    );
  });
  if (matchingArtifact) return matchingArtifact;
  if (["json", "scope_report", "test_cases"].includes(outputType)) {
    return `${outputId}.json`;
  }
  return "";
}

export function outputSchemaForSpec(
  outputId: string,
  allSchemas: Record<string, unknown>,
): Record<string, unknown> | null {
  const direct = allSchemas[outputId];
  if (direct && typeof direct === "object" && !Array.isArray(direct)) {
    return direct as Record<string, unknown>;
  }
  const builtin = (DEFAULT_BUILDER_OUTPUT_SCHEMAS as Record<string, unknown>)[
    outputId
  ];
  if (builtin && typeof builtin === "object" && !Array.isArray(builtin)) {
    return builtin as Record<string, unknown>;
  }
  const normalizedOutputId = outputId.replace(/[-_\s]/g, "").toLowerCase();
  const alias =
    normalizedOutputId.includes("sfmea")
      ? "sfmea"
      : normalizedOutputId.includes("blackbox") ||
          normalizedOutputId.includes("blackcase") ||
          normalizedOutputId.includes("testcase") ||
          normalizedOutputId.includes("cases")
        ? "black_box_cases"
        : normalizedOutputId.includes("evidence")
          ? "code_evidence"
          : normalizedOutputId.includes("scope")
            ? "source_scope"
            : "";
  const aliasedBuiltin = alias
    ? (DEFAULT_BUILDER_OUTPUT_SCHEMAS as Record<string, unknown>)[alias]
    : null;
  if (
    aliasedBuiltin &&
    typeof aliasedBuiltin === "object" &&
    !Array.isArray(aliasedBuiltin)
  ) {
    return aliasedBuiltin as Record<string, unknown>;
  }
  const wildcard = allSchemas["*"];
  if (wildcard && typeof wildcard === "object" && !Array.isArray(wildcard)) {
    return wildcard as Record<string, unknown>;
  }
  return null;
}

export function outputEvidenceMappingForSpec(
  outputId: string,
  allMappings: Record<string, unknown>,
): Record<string, unknown> | null {
  const direct = allMappings[outputId];
  if (direct && typeof direct === "object" && !Array.isArray(direct)) {
    return direct as Record<string, unknown>;
  }
  const wildcard = allMappings["*"];
  if (wildcard && typeof wildcard === "object" && !Array.isArray(wildcard)) {
    return wildcard as Record<string, unknown>;
  }
  return null;
}

export function outputSemanticImportForSpec(
  outputId: string,
  outputType: string,
  allMappings: Record<string, unknown>,
): Record<string, unknown> | null {
  const direct = allMappings[outputId];
  if (direct && typeof direct === "object" && !Array.isArray(direct)) {
    return direct as Record<string, unknown>;
  }
  const byType = allMappings[`type:${outputType}`];
  if (byType && typeof byType === "object" && !Array.isArray(byType)) {
    return byType as Record<string, unknown>;
  }
  const wildcard = allMappings["*"];
  if (wildcard && typeof wildcard === "object" && !Array.isArray(wildcard)) {
    return wildcard as Record<string, unknown>;
  }
  return null;
}

export function workflowInputsFromJson(value: string): Array<Record<string, unknown>> {
  try {
    const payload = parseJsonObject(value);
    return Array.isArray(payload.inputs)
      ? payload.inputs.filter((item): item is Record<string, unknown> =>
          Boolean(item && typeof item === "object" && !Array.isArray(item)),
        )
      : [];
  } catch {
    return [];
  }
}

export function workflowOutputsFromJson(value: string): Array<Record<string, unknown>> {
  try {
    const payload = parseJsonObject(value);
    return Array.isArray(payload.outputs)
      ? payload.outputs.filter((item): item is Record<string, unknown> =>
          Boolean(item && typeof item === "object" && !Array.isArray(item)),
        )
      : [];
  } catch {
    return [];
  }
}

export function workflowStepsFromJson(value: string): Array<Record<string, unknown>> {
  try {
    const payload = parseJsonObject(value);
    return Array.isArray(payload.steps)
      ? payload.steps.filter((item): item is Record<string, unknown> =>
          Boolean(item && typeof item === "object" && !Array.isArray(item)),
        )
      : [];
  } catch {
    return [];
  }
}

export function workflowOutputDisplayName(output: Record<string, unknown>): string {
  return String(output.label ?? output.id ?? output.artifact ?? "输出");
}

export function artifactShortName(path: string): string {
  return path.split("/").filter(Boolean).at(-1) || path;
}

export type WorkflowDraftAudit = {
  status: "ready" | "warning" | "invalid";
  inputCount: number;
  stepCount: number;
  agentStepCount: number;
  outputCount: number;
  evidenceMemoryOutputCount: number;
  semanticImportOutputCount: number;
  requiredArtifacts: string[];
  warnings: string[];
  blocking: string[];
};

export function workflowDraftAudit(value: string): WorkflowDraftAudit {
  const empty: WorkflowDraftAudit = {
    status: "invalid",
    inputCount: 0,
    stepCount: 0,
    agentStepCount: 0,
    outputCount: 0,
    evidenceMemoryOutputCount: 0,
    semanticImportOutputCount: 0,
    requiredArtifacts: [],
    warnings: [],
    blocking: [],
  };
  let payload: Record<string, unknown>;
  try {
    payload = parseJsonObject(value);
  } catch (error) {
    return {
      ...empty,
      blocking: [
        error instanceof Error ? error.message : "Workflow JSON is invalid",
      ],
    };
  }
  const inputs = Array.isArray(payload.inputs)
    ? payload.inputs.filter((item): item is Record<string, unknown> =>
        Boolean(item && typeof item === "object" && !Array.isArray(item)),
      )
    : [];
  const steps = Array.isArray(payload.steps)
    ? payload.steps.filter((item): item is Record<string, unknown> =>
        Boolean(item && typeof item === "object" && !Array.isArray(item)),
      )
    : [];
  const outputs = Array.isArray(payload.outputs)
    ? payload.outputs.filter((item): item is Record<string, unknown> =>
        Boolean(item && typeof item === "object" && !Array.isArray(item)),
      )
    : [];
  const stepIds = new Set(
    steps.map((step) => String(step.id ?? "")).filter(Boolean),
  );
  const warnings: string[] = [];
  const blocking: string[] = [];
  if (!String(payload.id ?? "").trim())
    blocking.push("workflow id is required");
  if (!String(payload.name ?? "").trim())
    warnings.push("workflow name is empty");
  if (steps.length === 0) blocking.push("workflow needs at least one step");
  if (outputs.length === 0) warnings.push("workflow has no declared outputs");

  const agentSteps = steps.filter(
    (step) => String(step.type ?? "") === "agent_task",
  );
  const requiredArtifacts = agentSteps.flatMap((step) =>
    Array.isArray(step.required_artifacts)
      ? step.required_artifacts.map((item) => String(item)).filter(Boolean)
      : [],
  );
  for (const step of agentSteps) {
    if (!String(step.provider ?? "").trim()) {
      blocking.push(
        `agent step ${String(step.id ?? "agent_task")} is missing provider`,
      );
    }
    if (
      !Array.isArray(step.required_artifacts) ||
      step.required_artifacts.length === 0
    ) {
      warnings.push(
        `agent step ${String(step.id ?? "agent_task")} has no required_artifacts`,
      );
    }
  }
  for (const output of outputs) {
    const outputId = String(output.id ?? "output");
    const from = String(output.from ?? "");
    const type = String(output.type ?? "");
    if (from && !stepIds.has(from)) {
      blocking.push(`output ${outputId} references unknown step ${from}`);
    }
    if (
      ["json", "scope_report", "test_cases"].includes(type) &&
      !String(output.artifact ?? "")
    ) {
      warnings.push(`output ${outputId} has no artifact path`);
    }
    const evidenceMemory = output.evidence_memory;
    if (
      evidenceMemory &&
      typeof evidenceMemory === "object" &&
      !Array.isArray(evidenceMemory) &&
      !String((evidenceMemory as Record<string, unknown>).kind ?? "").trim()
    ) {
      warnings.push(`output ${outputId} evidence_memory has no kind`);
    }
  }
  const status =
    blocking.length > 0 ? "invalid" : warnings.length > 0 ? "warning" : "ready";
  return {
    status,
    inputCount: inputs.length,
    stepCount: steps.length,
    agentStepCount: agentSteps.length,
    outputCount: outputs.length,
    evidenceMemoryOutputCount: outputs.filter((output) =>
      Boolean(output.evidence_memory),
    ).length,
    semanticImportOutputCount: outputs.filter((output) =>
      Boolean(output.semantic_import),
    ).length,
    requiredArtifacts: Array.from(new Set(requiredArtifacts)),
    warnings,
    blocking,
  };
}

export function inputTextValue(
  inputs: Record<string, unknown>,
  input: Record<string, unknown>,
): string {
  const inputId = String(input.id ?? "");
  const inputType = String(input.type ?? "");
  const value = inputs[inputId];
  if (inputType === "file_set") {
    if (Array.isArray(value)) {
      return value
        .map((item) =>
          item && typeof item === "object" && !Array.isArray(item)
            ? String((item as Record<string, unknown>).path ?? "")
            : String(item ?? ""),
        )
        .filter(Boolean)
        .join("\n");
    }
    return String(value ?? "");
  }
  if (isFileLikeWorkflowInput(inputType)) {
    if (value && typeof value === "object" && !Array.isArray(value)) {
      return String((value as Record<string, unknown>).path ?? "");
    }
    return String(value ?? "");
  }
  return typeof value === "string"
    ? value
    : value == null
      ? ""
      : JSON.stringify(value);
}

export function updateInputsJsonValue(
  inputsJson: string,
  input: Record<string, unknown>,
  rawValue: string,
): string {
  const payload = parseJsonObject(inputsJson || "{}");
  const inputId = String(input.id ?? "");
  const inputType = String(input.type ?? "");
  if (!inputId) return inputsJson;
  if (inputType === "file_set") {
    payload[inputId] = parseCommaSeparated(rawValue.replace(/\r?\n/g, ",")).map(
      (path) => ({
        path,
      }),
    );
  } else if (isPatchLikeWorkflowInput(inputType)) {
    payload[inputId] = rawValue;
  } else if (isFileLikeWorkflowInput(inputType)) {
    payload[inputId] = rawValue.trim() ? { path: rawValue.trim() } : "";
  } else if (inputType === "boolean") {
    payload[inputId] = rawValue === "true";
  } else if (inputType === "number") {
    payload[inputId] = rawValue.trim() ? Number(rawValue) : "";
  } else {
    payload[inputId] = rawValue;
  }
  return pretty(payload);
}

export function isFileLikeWorkflowInput(inputType: string): boolean {
  return ["file", "coverage_report"].includes(inputType);
}

export function isPatchLikeWorkflowInput(inputType: string): boolean {
  return ["patch", "diff"].includes(inputType);
}

export function semanticCasesFromLines({
  feature,
  module,
  text,
}: {
  feature: string;
  module: string;
  text: string;
}): Record<string, unknown> {
  const safeFeature = feature.trim() || "Imported feature";
  const safeModule = module.trim() || "module";
  const cases = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line, index) => {
      const [scenarioText, expectedText] = line.split(/\s*->\s*/, 2);
      const caseSuffix =
        scenarioText
          .toLowerCase()
          .replace(/[^a-z0-9]+/g, "_")
          .replace(/^_+|_+$/g, "")
          .slice(0, 48) || `case_${index + 1}`;
      return {
        case_id: `${safeModule}_${caseSuffix}_${index + 1}`,
        feature: safeFeature,
        module: safeModule,
        test_level: "black_box",
        scenario: scenarioText || line,
        terms: Array.from(
          new Set([
            ...safeFeature.split(/\s+/),
            ...safeModule.split(/[/_.\-\s]+/),
          ]),
        ).filter(Boolean),
        tags: ["imported_semantic_case"],
        preconditions: [],
        actions: [scenarioText || line],
        expected: [
          expectedText ||
            "Expected observable behavior matches the existing feature case.",
        ],
        assertion_style:
          "Prefer existing black-box terminology, observable status, logs, counters, and lifecycle checks.",
        source_ref: "workbench_semantic_text_import",
      };
    });
  return {
    defaults: {
      feature: safeFeature,
      module: safeModule,
      test_level: "black_box",
    },
    source_ref: "workbench_semantic_text_import",
    cases,
  };
}

export function isBulkSemanticImportPayload(value: unknown): boolean {
  if (Array.isArray(value)) return true;
  if (!value || typeof value !== "object") return false;
  const payload = value as Record<string, unknown>;
  return Array.isArray(payload.cases) || Array.isArray(payload.items);
}

export function fastContextDecisionSummary(
  taskBundle: Record<string, unknown>,
): string {
  const decisions = taskBundle.context_discovery_decision;
  if (!decisions || typeof decisions !== "object" || Array.isArray(decisions)) {
    return "";
  }
  const fastContext = (decisions as Record<string, unknown>)["fast-context"];
  if (
    !fastContext ||
    typeof fastContext !== "object" ||
    Array.isArray(fastContext)
  ) {
    return "";
  }
  const decision = fastContext as Record<string, unknown>;
  if (decision.codetalk_callable === true) {
    return "fast-context: CodeTalk callable";
  }
  const fallbackPath = Array.isArray(decision.fallback_path)
    ? decision.fallback_path.map((item) => String(item)).filter(Boolean)
    : [];
  const lastFallback = fallbackPath[fallbackPath.length - 1] || "local_search";
  return `fast-context: fallback to ${lastFallback}`;
}

export type InputContextFileSummary = {
  inputId: string;
  kind: string;
  filename: string;
  suffix: string;
  chunkCount: number;
  textTruncated: boolean;
  parseWarnings: string[];
};

export type InputContextSummary = {
  fileCount: number;
  inputs: InputContextFileSummary[];
};

export type AgentMcpRequestSummary = {
  inputId: string;
  inputType: string;
  credentialOwner: string;
  codetalkFetchAllowed: boolean;
  mcpProfiles: string[];
  requiredArtifacts: string[];
};

export type ProviderReadinessSummary = {
  status: string;
  repoStatus: string;
  blockingReasons: string[];
  warnings: string[];
  agentProviders: Array<{
    provider: string;
    status: string;
    reason: string;
    startupProbeEndpoint: string;
    manualProbeCommand: string;
    configuredCommand: string;
    usedFallback: boolean;
    deploymentTaskProbeStatus: string;
    deploymentProbeId: string;
    deploymentEvidenceConflict: boolean;
  }>;
  codetalkProviders: Array<{
    provider: string;
    status: string;
    nextCheck: string;
  }>;
};

export function inputContextSummary(
  taskBundle: Record<string, unknown>,
): InputContextSummary | null {
  const inputContext = taskBundle.input_context;
  if (
    !inputContext ||
    typeof inputContext !== "object" ||
    Array.isArray(inputContext)
  ) {
    return null;
  }
  const payload = inputContext as Record<string, unknown>;
  const rawInputs = Array.isArray(payload.inputs) ? payload.inputs : [];
  const inputs = rawInputs.flatMap((item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return [];
    const rawInput = item as Record<string, unknown>;
    const rawFiles = Array.isArray(rawInput.files)
      ? rawInput.files
      : [rawInput];
    return rawFiles
      .filter((file): file is Record<string, unknown> =>
        Boolean(file && typeof file === "object" && !Array.isArray(file)),
      )
      .map((file) => ({
        inputId: String(file.input_id ?? rawInput.input_id ?? ""),
        kind: String(file.kind ?? rawInput.kind ?? ""),
        filename: String(
          file.filename ?? file.original_path ?? file.copied_path ?? "",
        ),
        suffix: String(file.suffix ?? ""),
        chunkCount: Number(file.chunk_count ?? 0) || 0,
        textTruncated: file.text_truncated === true,
        parseWarnings: Array.isArray(file.parse_warnings)
          ? file.parse_warnings
              .map((warning) => String(warning))
              .filter(Boolean)
          : [],
      }))
      .filter((file) => file.filename || file.inputId);
  });
  const fileCount =
    Number(payload.file_count ?? inputs.length) || inputs.length;
  if (!fileCount && inputs.length === 0) return null;
  return { fileCount, inputs };
}

export function agentMcpRequestSummary(
  taskBundle: Record<string, unknown>,
): AgentMcpRequestSummary[] {
  const rawRequests = Array.isArray(taskBundle.agent_mcp_requests)
    ? taskBundle.agent_mcp_requests
    : [];
  return rawRequests.flatMap((item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return [];
    const request = item as Record<string, unknown>;
    const artifactValidation =
      request.artifact_validation &&
      typeof request.artifact_validation === "object" &&
      !Array.isArray(request.artifact_validation)
        ? (request.artifact_validation as Record<string, unknown>)
        : {};
    return [
      {
        inputId: String(request.input_id ?? ""),
        inputType: String(request.input_type ?? ""),
        credentialOwner: String(request.credential_owner ?? ""),
        codetalkFetchAllowed: request.codetalk_fetch_allowed === true,
        mcpProfiles: Array.isArray(request.mcp_profiles)
          ? request.mcp_profiles.map((value) => String(value)).filter(Boolean)
          : [],
        requiredArtifacts: Array.isArray(artifactValidation.required_artifacts)
          ? artifactValidation.required_artifacts
              .map((value) => String(value))
              .filter(Boolean)
          : [],
      },
    ];
  });
}

export function providerReadinessSummary(
  taskBundle: Record<string, unknown>,
): ProviderReadinessSummary | null {
  const raw = taskBundle.provider_readiness;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const readiness = raw as Record<string, unknown>;
  const summary =
    readiness.summary &&
    typeof readiness.summary === "object" &&
    !Array.isArray(readiness.summary)
      ? (readiness.summary as Record<string, unknown>)
      : {};
  const repo =
    readiness.repo &&
    typeof readiness.repo === "object" &&
    !Array.isArray(readiness.repo)
      ? (readiness.repo as Record<string, unknown>)
      : {};
  const agentProviders = Object.entries(
    readiness.agent_cli_providers &&
      typeof readiness.agent_cli_providers === "object" &&
      !Array.isArray(readiness.agent_cli_providers)
      ? (readiness.agent_cli_providers as Record<string, unknown>)
      : {},
  ).flatMap(([provider, value]) => {
    if (!value || typeof value !== "object" || Array.isArray(value)) return [];
    const payload = value as Record<string, unknown>;
    const deploymentEvidence =
      payload.deployment_evidence &&
      typeof payload.deployment_evidence === "object" &&
      !Array.isArray(payload.deployment_evidence)
        ? (payload.deployment_evidence as Record<string, unknown>)
        : {};
    return [
      {
        provider,
        status: String(payload.status ?? "unknown"),
        reason: String(payload.reason ?? ""),
        startupProbeEndpoint: String(payload.startup_probe_endpoint ?? ""),
        manualProbeCommand: String(payload.manual_probe_command ?? ""),
        configuredCommand: String(
          payload.configured_command ?? payload.command ?? "",
        ),
        usedFallback: Boolean(payload.used_fallback ?? false),
        deploymentTaskProbeStatus: String(
          deploymentEvidence.task_probe_status ?? "",
        ),
        deploymentProbeId: String(deploymentEvidence.probe_id ?? ""),
        deploymentEvidenceConflict: Boolean(
          payload.deployment_evidence_conflict ?? false,
        ),
      },
    ];
  });
  const codetalkProviders = Object.entries(
    readiness.codetalk_providers &&
      typeof readiness.codetalk_providers === "object" &&
      !Array.isArray(readiness.codetalk_providers)
      ? (readiness.codetalk_providers as Record<string, unknown>)
      : {},
  ).flatMap(([provider, value]) => {
    if (!value || typeof value !== "object" || Array.isArray(value)) return [];
    const payload = value as Record<string, unknown>;
    return [
      {
        provider,
        status: String(payload.status ?? "unknown"),
        nextCheck: String(payload.next_check ?? ""),
      },
    ];
  });
  return {
    status: String(summary.status ?? "unknown"),
    repoStatus: String(repo.status ?? "unknown"),
    blockingReasons: Array.isArray(summary.blocking_reasons)
      ? summary.blocking_reasons.map((item) => String(item)).filter(Boolean)
      : [],
    warnings: Array.isArray(summary.warnings)
      ? summary.warnings.map((item) => String(item)).filter(Boolean)
      : [],
    agentProviders,
    codetalkProviders,
  };
}

export type EvidenceValidationSummary = {
  acceptedCount: number;
  rejectedCount: number;
  acceptedDetails: Array<{
    artifact: string;
    sha256: string;
    sourceStepId: string;
  }>;
  rejectedDetails: Array<{
    artifact: string;
    reason: string;
    sourceStepId: string;
  }>;
};

export type WorkflowOutputMaterializationSummary = {
  evidenceCount: number;
  rejectedCount: number;
  workflowOutputsSha: string;
  outputCount: number;
  auditSummary: {
    declaredOutputCount: number;
    evidenceMemoryDeclaredCount: number;
    materializedOutputCount: number;
    rejectedOutputCount: number;
    rejectedItemCount: number;
  };
  auditOutputs: Array<{
    outputId: string;
    declaredType: string;
    artifact: string;
    from: string;
    producedStatus: string;
    materializationStatus: string;
    evidenceMemoryDeclared: boolean;
    mappingKind: string;
    materializedCount: number;
    rejectedCount: number;
    rejectionReasons: string[];
  }>;
  materializedEvidence: Array<{
    evidenceId: string;
    kind: string;
    subjectKey: string;
    outputId: string;
    sourceStepId: string;
    mappingKind: string;
  }>;
  firstRejected?: {
    output: string;
    reason: string;
    status: string;
    schemaErrorCount: number;
  };
};

export type ReplayPlanSummary = {
  replayStatus: string;
  provider: string;
  turnId: string;
  promptSource: string;
  promptTransport: string;
  cwd: string;
  timeoutSec: number;
  readonlyRequired: boolean;
  validatesOutputs: boolean;
  hashCount: number;
  taskBundleSha: string;
  executionInputSha: string;
  contractSha: string;
};

export type ExecutionInputSummary = {
  provider: string;
  turnId: string;
  promptTransport: string;
  promptTransportReason: string;
  timeoutSec: number;
  cwd: string;
  stdinRedacted: boolean;
  stdinSha: string;
  readonlyEnv: string;
  outputContractSha: string;
};

export type BlackBoxGenerationPolicySummary = {
  termCount: number;
  caseCount: number;
  firstCaseId: string;
  firstTerms: string[];
  allowedUses: string[];
  mustNotUse: string[];
  authorityRule: string;
};

export type MemoryArtifactSummary = {
  kind: "memory_retrieval" | "context_bundle";
  query: string;
  evidenceCount: number;
  deploymentCount: number;
  semanticCount: number;
  sourceSliceCount: number;
  firstSubject: string;
  firstReuseReason: string;
  firstDeploymentSubject: string;
};

export type InputMaterialsSummary = {
  materialCount: number;
  readOrder: string[];
  firstInputId: string;
  firstRole: string;
  firstFilename: string;
  firstSha: string;
  firstChunksPath: string;
  mustRead: boolean;
  materialsAreSourceTruth: boolean;
};

export type FailureRetryContextSummary = {
  stepId: string;
  failureKind: string;
  retryable: boolean;
  exitCode: string;
  missingArtifacts: string[];
  stdoutExcerpt: string;
  stderrExcerpt: string;
  mustProduceArtifacts: string[];
  doNotRepeat: string[];
};

export function commandResolutionLines(
  resolution?: AgentCommandResolutionDetail,
): string[] {
  if (!resolution) return [];
  const lines = [
    resolution.method ? `method:${resolution.method}` : "",
    resolution.which ? `which:${resolution.which}` : "",
    resolution.where_exe ? `where:${resolution.where_exe}` : "",
    typeof resolution.where_returncode === "number"
      ? `where_exit:${resolution.where_returncode}`
      : "",
    resolution.common_dir_path ? `common:${resolution.common_dir_path}` : "",
    resolution.powershell_get_command
      ? `ps:${resolution.powershell_get_command}`
      : "",
    resolution.path ? `path:${resolution.path}` : "",
  ].filter(Boolean);
  if (resolution.where_stderr && lines.length < 6) {
    lines.push(`where_stderr:${resolution.where_stderr}`);
  }
  return lines.slice(0, 6);
}

export type AcceptanceProviderIssue = {
  provider: string;
  status: string;
  reason: string;
  startupProbeEndpoint: string;
  usedFallback: boolean;
  deploymentTaskProbeStatus: string;
  deploymentProbeId: string;
  deploymentEvidenceConflict: boolean;
};

export type AcceptanceWorkflowOutputIssue = {
  outputId: string;
  status: string;
  reason: string;
  artifact: string;
  schemaErrorCount: number;
};

export type AcceptanceInstructionPolicyIssue = {
  id: string;
  label: string;
  reason: string;
  relativePath: string;
  expectedFiles: string[];
};

export type AcceptanceInputRedactionIssue = {
  id: string;
  label: string;
  reason: string;
  relativePath: string;
  stdinSha: string;
};

export function acceptanceProviderIssues(
  audit: WorkbenchAcceptanceAudit | null,
): AcceptanceProviderIssue[] {
  if (!audit) return [];
  return audit.missing_required
    .filter((item) =>
      String(item.id ?? "").startsWith("provider_readiness_agent:"),
    )
    .map((item) => ({
      provider: String(
        item.provider ?? String(item.id ?? "").split(":")[1] ?? "agent",
      ),
      status: String(item.provider_status ?? item.status ?? "unknown"),
      reason: String(item.reason ?? ""),
      startupProbeEndpoint: String(item.startup_probe_endpoint ?? ""),
      usedFallback: Boolean(item.used_fallback ?? false),
      deploymentTaskProbeStatus: String(
        item.deployment_task_probe_status ?? "",
      ),
      deploymentProbeId: String(item.deployment_probe_id ?? ""),
      deploymentEvidenceConflict: Boolean(
        item.deployment_evidence_conflict ?? false,
      ),
    }))
    .filter((item) => item.provider);
}

export function acceptanceCodetalkProviderIssues(
  audit: WorkbenchAcceptanceAudit | null,
): AcceptanceProviderIssue[] {
  if (!audit) return [];
  return audit.missing_recommended
    .filter((item) =>
      String(item.id ?? "").startsWith("provider_readiness_codetalk:"),
    )
    .map((item) => ({
      provider: String(
        item.provider ?? String(item.id ?? "").split(":")[1] ?? "provider",
      ),
      status: String(item.provider_status ?? item.status ?? "unknown"),
      reason: String(item.reason ?? ""),
      startupProbeEndpoint: String(
        item.startup_probe_endpoint ?? item.next_check ?? "",
      ),
      usedFallback: false,
      deploymentTaskProbeStatus: "",
      deploymentProbeId: "",
      deploymentEvidenceConflict: false,
    }))
    .filter((item) => item.provider);
}

export function acceptanceWorkflowOutputIssues(
  audit: WorkbenchAcceptanceAudit | null,
): AcceptanceWorkflowOutputIssue[] {
  if (!audit) return [];
  return audit.missing_required
    .filter((item) => String(item.id ?? "").startsWith("workflow_output:"))
    .map((item) => ({
      outputId: String(
        item.output_id ?? String(item.id ?? "").split(":")[1] ?? "output",
      ),
      status: String(item.output_status ?? item.status ?? "unknown"),
      reason: String(item.reason ?? ""),
      artifact: String(item.artifact ?? ""),
      schemaErrorCount: Array.isArray(item.schema_errors)
        ? item.schema_errors.length
        : 0,
    }))
    .filter((item) => item.outputId);
}

export function acceptanceInstructionPolicyIssues(
  audit: WorkbenchAcceptanceAudit | null,
): AcceptanceInstructionPolicyIssue[] {
  if (!audit) return [];
  return audit.missing_required
    .filter((item) => {
      const id = String(item.id ?? "");
      return (
        id.startsWith("agent_instruction_policy:") ||
        id.startsWith("agent_turn_instruction_policy:")
      );
    })
    .map((item) => {
      const id = String(item.id ?? "");
      const parts = id.split(":");
      const expectedFiles = Array.isArray(item.expected_files)
        ? item.expected_files
            .filter((file): file is Record<string, unknown> =>
              Boolean(file && typeof file === "object" && !Array.isArray(file)),
            )
            .map((file) => String(file.relative_path ?? ""))
            .filter(Boolean)
        : [];
      const label = id.startsWith("agent_turn_instruction_policy:")
        ? `${parts[1] ?? "step"} ${parts[2] ?? "turn"} ${parts[3] ?? "artifact"}`
        : `${parts[1] ?? "step"} ${parts[2] ?? "artifact"}`;
      return {
        id,
        label,
        reason: String(item.reason ?? ""),
        relativePath: String(item.relative_path ?? ""),
        expectedFiles,
      };
    });
}

export function acceptanceInputRedactionIssues(
  audit: WorkbenchAcceptanceAudit | null,
): AcceptanceInputRedactionIssue[] {
  if (!audit) return [];
  return audit.missing_required
    .filter((item) => {
      const id = String(item.id ?? "");
      return (
        id.startsWith("agent_stdin_redaction:") ||
        id.startsWith("agent_turn_stdin_redaction:")
      );
    })
    .map((item) => {
      const id = String(item.id ?? "");
      const parts = id.split(":");
      const label = id.startsWith("agent_turn_stdin_redaction:")
        ? `${parts[1] ?? "step"} ${parts[2] ?? "turn"} ${parts[3] ?? "artifact"}`
        : `${parts[1] ?? "step"} ${parts[2] ?? "artifact"}`;
      return {
        id,
        label,
        reason: String(item.reason ?? ""),
        relativePath: String(item.relative_path ?? ""),
        stdinSha: String(item.stdin_json_sha256 ?? ""),
      };
    });
}

export function evidenceValidationSummary(
  artifact: WorkbenchTaskArtifactContent,
): EvidenceValidationSummary | null {
  if (!artifact.is_text || !artifact.content.trim()) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(artifact.content);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
    return null;
  const payload = parsed as Record<string, unknown>;
  if (
    artifact.kind !== "evidence_validation" &&
    !("accepted_artifact_details" in payload) &&
    !("rejected_artifact_details" in payload)
  ) {
    return null;
  }
  const acceptedDetails = Array.isArray(payload.accepted_artifact_details)
    ? payload.accepted_artifact_details
        .filter((item): item is Record<string, unknown> =>
          Boolean(item && typeof item === "object" && !Array.isArray(item)),
        )
        .map((item) => ({
          artifact: String(item.artifact ?? ""),
          sha256: String(item.sha256 ?? ""),
          sourceStepId: String(item.source_step_id ?? ""),
        }))
        .filter((item) => item.artifact)
    : [];
  const rejectedDetails = Array.isArray(payload.rejected_artifact_details)
    ? payload.rejected_artifact_details
        .filter((item): item is Record<string, unknown> =>
          Boolean(item && typeof item === "object" && !Array.isArray(item)),
        )
        .map((item) => ({
          artifact: String(item.artifact ?? ""),
          reason: String(item.reason ?? ""),
          sourceStepId: String(item.source_step_id ?? ""),
        }))
        .filter((item) => item.artifact || item.reason)
    : [];
  return {
    acceptedCount:
      Number(payload.accepted_count ?? acceptedDetails.length) || 0,
    rejectedCount:
      Number(payload.rejected_count ?? rejectedDetails.length) || 0,
    acceptedDetails,
    rejectedDetails,
  };
}

export function workflowOutputMaterializationSummary(
  artifact: WorkbenchTaskArtifactContent,
): WorkflowOutputMaterializationSummary | null {
  if (!artifact.is_text || !artifact.content.trim()) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(artifact.content);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
    return null;
  const payload = parsed as Record<string, unknown>;
  if (
    artifact.kind !== "workflow_output_materialization" &&
    !("workflow_outputs_artifact" in payload)
  ) {
    return null;
  }
  const workflowOutputsArtifact =
    payload.workflow_outputs_artifact &&
    typeof payload.workflow_outputs_artifact === "object" &&
    !Array.isArray(payload.workflow_outputs_artifact)
      ? (payload.workflow_outputs_artifact as Record<string, unknown>)
      : {};
  const rejectedOutputs = Array.isArray(payload.rejected_outputs)
    ? payload.rejected_outputs
    : [];
  const firstRejectedPayload =
    rejectedOutputs[0] &&
    typeof rejectedOutputs[0] === "object" &&
    !Array.isArray(rejectedOutputs[0])
      ? (rejectedOutputs[0] as Record<string, unknown>)
      : null;
  const schemaErrors = Array.isArray(firstRejectedPayload?.schema_errors)
    ? firstRejectedPayload.schema_errors
    : [];
  const materializedEvidence = Array.isArray(payload.materialized_evidence)
    ? payload.materialized_evidence
        .filter((item): item is Record<string, unknown> =>
          Boolean(item && typeof item === "object" && !Array.isArray(item)),
        )
        .map((item) => ({
          evidenceId: String(item.evidence_id ?? ""),
          kind: String(item.kind ?? ""),
          subjectKey: String(item.subject_key ?? ""),
          outputId: String(item.output_id ?? ""),
          sourceStepId: String(item.source_step_id ?? ""),
          mappingKind: String(item.mapping_kind ?? ""),
        }))
        .filter((item) => item.evidenceId || item.kind || item.subjectKey)
    : [];
  const audit =
    payload.materialization_audit &&
    typeof payload.materialization_audit === "object" &&
    !Array.isArray(payload.materialization_audit)
      ? (payload.materialization_audit as Record<string, unknown>)
      : {};
  const auditSummary =
    audit.summary &&
    typeof audit.summary === "object" &&
    !Array.isArray(audit.summary)
      ? (audit.summary as Record<string, unknown>)
      : {};
  const auditOutputs = Array.isArray(audit.outputs)
    ? audit.outputs
        .filter((item): item is Record<string, unknown> =>
          Boolean(item && typeof item === "object" && !Array.isArray(item)),
        )
        .map((item) => {
          const mapping =
            item.evidence_memory_mapping &&
            typeof item.evidence_memory_mapping === "object" &&
            !Array.isArray(item.evidence_memory_mapping)
              ? (item.evidence_memory_mapping as Record<string, unknown>)
              : {};
          return {
            outputId: String(item.output_id ?? ""),
            declaredType: String(item.declared_type ?? ""),
            artifact: String(item.artifact ?? ""),
            from: String(item.from ?? ""),
            producedStatus: String(item.produced_status ?? ""),
            materializationStatus: String(item.materialization_status ?? ""),
            evidenceMemoryDeclared: Boolean(item.evidence_memory_declared),
            mappingKind: String(mapping.kind ?? ""),
            materializedCount: Number(item.materialized_count ?? 0) || 0,
            rejectedCount: Number(item.rejected_count ?? 0) || 0,
            rejectionReasons: Array.isArray(item.rejection_reasons)
              ? item.rejection_reasons
                  .map((reason) => String(reason))
                  .filter(Boolean)
              : [],
          };
        })
        .filter((item) => item.outputId)
    : [];
  return {
    evidenceCount: Number(payload.evidence_count ?? 0) || 0,
    rejectedCount: rejectedOutputs.length,
    workflowOutputsSha: String(workflowOutputsArtifact.sha256 ?? ""),
    outputCount: Number(workflowOutputsArtifact.output_count ?? 0) || 0,
    auditSummary: {
      declaredOutputCount: Number(auditSummary.declared_output_count ?? 0) || 0,
      evidenceMemoryDeclaredCount:
        Number(auditSummary.evidence_memory_declared_count ?? 0) || 0,
      materializedOutputCount:
        Number(auditSummary.materialized_output_count ?? 0) || 0,
      rejectedOutputCount: Number(auditSummary.rejected_output_count ?? 0) || 0,
      rejectedItemCount: Number(auditSummary.rejected_item_count ?? 0) || 0,
    },
    auditOutputs,
    materializedEvidence,
    firstRejected: firstRejectedPayload
      ? {
          output: String(firstRejectedPayload.output ?? ""),
          reason: String(firstRejectedPayload.reason ?? ""),
          status: String(firstRejectedPayload.output_status ?? ""),
          schemaErrorCount: schemaErrors.length,
        }
      : undefined,
  };
}

export function materializationAuditOutputs(
  result: MaterializeWorkflowOutputsResult,
): WorkflowOutputMaterializationSummary["auditOutputs"] {
  const outputs = result.materialization_audit?.outputs;
  if (!Array.isArray(outputs)) return [];
  return outputs
    .filter((item): item is Record<string, unknown> =>
      Boolean(item && typeof item === "object" && !Array.isArray(item)),
    )
    .map((item) => {
      const mapping =
        item.evidence_memory_mapping &&
        typeof item.evidence_memory_mapping === "object" &&
        !Array.isArray(item.evidence_memory_mapping)
          ? (item.evidence_memory_mapping as Record<string, unknown>)
          : {};
      return {
        outputId: String(item.output_id ?? ""),
        declaredType: String(item.declared_type ?? ""),
        artifact: String(item.artifact ?? ""),
        from: String(item.from ?? ""),
        producedStatus: String(item.produced_status ?? ""),
        materializationStatus: String(item.materialization_status ?? ""),
        evidenceMemoryDeclared: Boolean(item.evidence_memory_declared),
        mappingKind: String(mapping.kind ?? ""),
        materializedCount: Number(item.materialized_count ?? 0) || 0,
        rejectedCount: Number(item.rejected_count ?? 0) || 0,
        rejectionReasons: Array.isArray(item.rejection_reasons)
          ? item.rejection_reasons
              .map((reason) => String(reason))
              .filter(Boolean)
          : [],
      };
    })
    .filter((item) => item.outputId);
}

export function replayPlanSummary(
  artifact: WorkbenchTaskArtifactContent,
): ReplayPlanSummary | null {
  if (!artifact.is_text || !artifact.content.trim()) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(artifact.content);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
    return null;
  const payload = parsed as Record<string, unknown>;
  if (
    artifact.kind !== "agent_replay_plan" &&
    artifact.kind !== "agent_turn_replay_plan" &&
    !("replay_status" in payload)
  ) {
    return null;
  }
  const safety =
    payload.safety_boundary &&
    typeof payload.safety_boundary === "object" &&
    !Array.isArray(payload.safety_boundary)
      ? (payload.safety_boundary as Record<string, unknown>)
      : {};
  const hashes =
    payload.artifact_hashes &&
    typeof payload.artifact_hashes === "object" &&
    !Array.isArray(payload.artifact_hashes)
      ? (payload.artifact_hashes as Record<string, unknown>)
      : {};
  return {
    replayStatus: String(payload.replay_status ?? "unknown"),
    provider: String(payload.provider ?? ""),
    turnId: String(payload.turn_id ?? ""),
    promptSource: String(payload.prompt_source ?? ""),
    promptTransport: String(payload.prompt_transport ?? ""),
    cwd: String(payload.cwd ?? ""),
    timeoutSec: Number(payload.timeout_sec ?? 0) || 0,
    readonlyRequired: Boolean(safety.readonly_env_required ?? false),
    validatesOutputs: Boolean(safety.codetalk_validates_outputs ?? false),
    hashCount: Object.keys(hashes).length,
    taskBundleSha: String(
      hashes["task_bundle.json"] ?? hashes.task_bundle_sha256 ?? "",
    ),
    executionInputSha: String(
      hashes["execution_input.json"] ?? hashes.stdin_json_sha256 ?? "",
    ),
    contractSha: String(
      hashes["agent_output_contract.json"] ??
        hashes.agent_output_contract_sha256 ??
        "",
    ),
  };
}

export function executionInputSummary(
  artifact: WorkbenchTaskArtifactContent,
): ExecutionInputSummary | null {
  if (!artifact.is_text || !artifact.content.trim()) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(artifact.content);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
    return null;
  const payload = parsed as Record<string, unknown>;
  if (
    artifact.kind !== "agent_execution_input" &&
    artifact.kind !== "agent_turn_execution_input" &&
    !("stdin_redacted" in payload)
  ) {
    return null;
  }
  const envHints =
    payload.env_hints &&
    typeof payload.env_hints === "object" &&
    !Array.isArray(payload.env_hints)
      ? (payload.env_hints as Record<string, unknown>)
      : {};
  return {
    provider: String(payload.provider ?? ""),
    turnId: String(payload.turn_id ?? ""),
    promptTransport: String(payload.prompt_transport ?? ""),
    promptTransportReason: String(payload.prompt_transport_reason ?? ""),
    timeoutSec: Number(payload.timeout_sec ?? 0) || 0,
    cwd: String(payload.cwd ?? ""),
    stdinRedacted: Boolean(payload.stdin_redacted ?? false),
    stdinSha: String(payload.stdin_json_sha256 ?? ""),
    readonlyEnv: String(envHints.CODETALK_AGENT_READONLY ?? ""),
    outputContractSha: String(payload.agent_output_contract_sha256 ?? ""),
  };
}

export function blackBoxGenerationPolicySummary(
  artifact: WorkbenchTaskArtifactContent,
): BlackBoxGenerationPolicySummary | null {
  if (!artifact.is_text || !artifact.content.trim()) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(artifact.content);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
    return null;
  const payload = parsed as Record<string, unknown>;
  if (
    artifact.kind !== "black_box_generation_policy" &&
    !("semantic_terms" in payload)
  ) {
    return null;
  }
  const semanticTerms = Array.isArray(payload.semantic_terms)
    ? payload.semantic_terms.filter((item): item is Record<string, unknown> =>
        Boolean(item && typeof item === "object" && !Array.isArray(item)),
      )
    : [];
  const firstTerm = semanticTerms[0] ?? {};
  const firstTerms = Array.isArray(firstTerm.terms)
    ? firstTerm.terms.map((item) => String(item)).filter(Boolean)
    : [];
  return {
    termCount: Number(payload.semantic_term_count ?? 0) || firstTerms.length,
    caseCount: Number(payload.semantic_case_count ?? 0) || semanticTerms.length,
    firstCaseId: String(firstTerm.case_id ?? ""),
    firstTerms,
    allowedUses: Array.isArray(payload.allowed_uses)
      ? payload.allowed_uses.map((item) => String(item)).filter(Boolean)
      : [],
    mustNotUse: Array.isArray(payload.must_not_use_semantics_as)
      ? payload.must_not_use_semantics_as
          .map((item) => String(item))
          .filter(Boolean)
      : [],
    authorityRule: String(payload.authority_rule ?? ""),
  };
}

export function memoryArtifactSummary(
  artifact: WorkbenchTaskArtifactContent,
): MemoryArtifactSummary | null {
  if (!artifact.is_text || !artifact.content.trim()) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(artifact.content);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
    return null;
  const payload = parsed as Record<string, unknown>;
  const isMemoryRetrieval =
    artifact.kind === "memory_retrieval" ||
    "retrieved_count" in payload ||
    "deployment_retrieved_count" in payload;
  const isContextBundle =
    artifact.kind === "context_bundle" ||
    ("evidence" in payload && "semantic_cases" in payload);
  if (!isMemoryRetrieval && !isContextBundle) return null;
  const evidenceItems = Array.isArray(payload.items)
    ? payload.items
    : Array.isArray(payload.evidence)
      ? payload.evidence
      : [];
  const deploymentItems = Array.isArray(payload.deployment_items)
    ? payload.deployment_items
    : Array.isArray(payload.deployment_evidence)
      ? payload.deployment_evidence
      : [];
  const semanticItems = Array.isArray(payload.semantic_cases)
    ? payload.semantic_cases
    : [];
  const firstEvidence =
    evidenceItems[0] &&
    typeof evidenceItems[0] === "object" &&
    !Array.isArray(evidenceItems[0])
      ? (evidenceItems[0] as Record<string, unknown>)
      : {};
  const firstDeployment =
    deploymentItems[0] &&
    typeof deploymentItems[0] === "object" &&
    !Array.isArray(deploymentItems[0])
      ? (deploymentItems[0] as Record<string, unknown>)
      : {};
  const sourceSliceCount =
    Number(payload.source_slice_count ?? 0) ||
    evidenceItems.reduce((total, item) => {
      if (!item || typeof item !== "object" || Array.isArray(item))
        return total;
      const record = item as Record<string, unknown>;
      if (Array.isArray(record.source_slices))
        return total + record.source_slices.length;
      if (Array.isArray(record.source_slice_refs))
        return total + record.source_slice_refs.length;
      return total + (Number(record.source_slice_count ?? 0) || 0);
    }, 0);
  return {
    kind: isMemoryRetrieval ? "memory_retrieval" : "context_bundle",
    query: String(payload.query ?? ""),
    evidenceCount: Number(payload.retrieved_count ?? 0) || evidenceItems.length,
    deploymentCount:
      Number(payload.deployment_retrieved_count ?? 0) || deploymentItems.length,
    semanticCount:
      Number(payload.semantic_retrieved_count ?? 0) || semanticItems.length,
    sourceSliceCount,
    firstSubject: String(firstEvidence.subject_key ?? firstEvidence.path ?? ""),
    firstReuseReason: String(
      firstEvidence.reuse_reason ?? firstEvidence.reason ?? "",
    ),
    firstDeploymentSubject: String(
      firstDeployment.subject_key ??
        firstDeployment.provider ??
        firstDeployment.symbol ??
        "",
    ),
  };
}

export function inputMaterialsSummary(
  artifact: WorkbenchTaskArtifactContent,
): InputMaterialsSummary | null {
  if (!artifact.is_text || !artifact.content.trim()) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(artifact.content);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
    return null;
  const payload = parsed as Record<string, unknown>;
  if (
    artifact.kind !== "input_materials" &&
    payload.kind !== "input_materials"
  ) {
    return null;
  }
  const materials = Array.isArray(payload.materials)
    ? payload.materials.filter((item): item is Record<string, unknown> =>
        Boolean(item && typeof item === "object" && !Array.isArray(item)),
      )
    : [];
  const first = materials[0] ?? {};
  const rules =
    payload.rules &&
    typeof payload.rules === "object" &&
    !Array.isArray(payload.rules)
      ? (payload.rules as Record<string, unknown>)
      : {};
  return {
    materialCount: Number(payload.material_count ?? 0) || materials.length,
    readOrder: Array.isArray(payload.read_order)
      ? payload.read_order.map((item) => String(item)).filter(Boolean)
      : [],
    firstInputId: String(first.input_id ?? ""),
    firstRole: String(first.material_role ?? ""),
    firstFilename: String(first.filename ?? ""),
    firstSha: String(first.sha256 ?? ""),
    firstChunksPath: String(first.chunks_path ?? ""),
    mustRead: Boolean(rules.agent_must_read_materials ?? false),
    materialsAreSourceTruth: Boolean(rules.materials_are_source_truth ?? false),
  };
}

export function failureRetryContextSummary(
  artifact: WorkbenchTaskArtifactContent,
): FailureRetryContextSummary | null {
  if (!artifact.is_text || !artifact.content.trim()) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(artifact.content);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
    return null;
  const payload = parsed as Record<string, unknown>;
  if (
    artifact.kind !== "agent_failure_retry_context" &&
    payload.kind !== "agent_failure_retry_context"
  ) {
    return null;
  }
  const previousExecution =
    payload.previous_execution &&
    typeof payload.previous_execution === "object" &&
    !Array.isArray(payload.previous_execution)
      ? (payload.previous_execution as Record<string, unknown>)
      : {};
  const previousOutput =
    payload.previous_output &&
    typeof payload.previous_output === "object" &&
    !Array.isArray(payload.previous_output)
      ? (payload.previous_output as Record<string, unknown>)
      : {};
  const retryInstructions =
    payload.retry_instructions &&
    typeof payload.retry_instructions === "object" &&
    !Array.isArray(payload.retry_instructions)
      ? (payload.retry_instructions as Record<string, unknown>)
      : {};
  return {
    stepId: String(payload.step_id ?? ""),
    failureKind: String(payload.failure_kind ?? ""),
    retryable: Boolean(payload.retryable ?? false),
    exitCode: String(previousExecution.exit_code ?? ""),
    missingArtifacts: Array.isArray(payload.missing_artifacts)
      ? payload.missing_artifacts.map((item) => String(item)).filter(Boolean)
      : [],
    stdoutExcerpt: artifact.content_redacted
      ? ""
      : String(previousOutput.stdout_excerpt ?? ""),
    stderrExcerpt: artifact.content_redacted
      ? ""
      : String(previousOutput.stderr_excerpt ?? ""),
    mustProduceArtifacts: Array.isArray(
      retryInstructions.must_produce_artifacts,
    )
      ? retryInstructions.must_produce_artifacts
          .map((item) => String(item))
          .filter(Boolean)
      : [],
    doNotRepeat: Array.isArray(retryInstructions.do_not_repeat)
      ? retryInstructions.do_not_repeat
          .map((item) => String(item))
          .filter(Boolean)
      : [],
  };
}

export function rejectedOutputLabel(item: Record<string, unknown>): string {
  return String(
    item.output ??
      item.output_type ??
      item.path ??
      item.file_path ??
      item.card_id ??
      item.function_name ??
      "output",
  );
}

export function rejectedOutputReason(item: Record<string, unknown>): string {
  const reason = String(item.reason ?? item.validation_error ?? "rejected");
  const path =
    item.path || item.file_path ? String(item.path ?? item.file_path) : "";
  const cardId = item.card_id ? String(item.card_id) : "";
  const status = item.output_status ? String(item.output_status) : "";
  const details = [
    path ? `path:${path}` : "",
    cardId ? `card:${cardId}` : "",
    status ? `status:${status}` : "",
  ].filter(Boolean);
  return details.length > 0 ? `${reason} (${details.join(" / ")})` : reason;
}

export function evidenceAuditRefs(provenance: Record<string, unknown>): Array<{
  label: string;
  artifact: string;
  sha256: string;
}> {
  const refs: Array<{ key: string; label: string }> = [
    { key: "agent_replay_plan", label: "Replay" },
    { key: "agent_execution_input", label: "Input" },
    { key: "agent_execution_result", label: "Result" },
    { key: "workflow_outputs_artifact", label: "Output" },
    { key: "agent_output_contract", label: "Contract" },
  ];
  return refs
    .map(({ key, label }) => {
      const value = provenance[key];
      if (!value || typeof value !== "object" || Array.isArray(value)) {
        return null;
      }
      const payload = value as Record<string, unknown>;
      const artifact = String(payload.artifact ?? "");
      if (!artifact) return null;
      return {
        label,
        artifact,
        sha256: String(payload.sha256 ?? ""),
      };
    })
    .filter(
      (item): item is { label: string; artifact: string; sha256: string } =>
        Boolean(item),
    );
}

export const AUDIT_ARTIFACT_KIND_ORDER = [
  "task_bundle",
  "input_snapshot",
  "input_context",
  "input_materials",
  "input_file_metadata",
  "input_file_set_manifest",
  "input_parsed_text",
  "input_chunks",
  "input_original_file",
  "input_artifact",
  "agent_task_bundle",
  "agent_output_contract",
  "agent_provider_diagnostics",
  "agent_replay_plan",
  "agent_run_lifecycle",
  "agent_failure_recovery",
  "agent_failure_retry_context",
  "agent_turn_task_bundle",
  "agent_turn_output_contract",
  "agent_turn_provider_diagnostics",
  "agent_turn_execution_input",
  "agent_turn_execution_result",
  "agent_turn_replay_plan",
  "agent_turn_source_slice_requests",
  "agent_turn_source_slices",
  "agent_turn_raw_output",
  "agent_turn_run",
  "agent_instructions",
  "provider_snapshot",
  "provider_readiness",
  "workflow_contract",
  "agent_mcp_requests",
  "context_discovery_decision",
  "context_bundle",
  "output_schemas",
  "memory_retrieval",
  "source_read_chain",
  "evidence_consumption_trajectory",
  "degraded_retrieval",
  "evidence_validation",
  "workflow_outputs",
  "semantic_import_outputs",
  "workflow_output_materialization",
  "workflow_execution",
  "task_acceptance_audit",
  "task_rerun_plan",
  "task_rerun_execution",
  "task_rerun_history",
];

export function prioritizedAuditArtifacts(
  artifacts: WorkbenchTaskArtifact[],
): WorkbenchTaskArtifact[] {
  return [...artifacts].sort((left, right) => {
    const leftOutputRank = workflowOutputArtifactRank(left.relative_path);
    const rightOutputRank = workflowOutputArtifactRank(right.relative_path);
    if (leftOutputRank !== rightOutputRank) {
      return leftOutputRank - rightOutputRank;
    }
    const leftRank = AUDIT_ARTIFACT_KIND_ORDER.indexOf(left.kind);
    const rightRank = AUDIT_ARTIFACT_KIND_ORDER.indexOf(right.kind);
    const normalizedLeftRank =
      leftRank === -1 ? AUDIT_ARTIFACT_KIND_ORDER.length : leftRank;
    const normalizedRightRank =
      rightRank === -1 ? AUDIT_ARTIFACT_KIND_ORDER.length : rightRank;
    if (normalizedLeftRank !== normalizedRightRank) {
      return normalizedLeftRank - normalizedRightRank;
    }
    return left.relative_path.localeCompare(right.relative_path);
  });
}

export function workflowOutputArtifactRank(relativePath: string): number {
  const name = relativePath.split("/").pop() ?? relativePath;
  const order = [
    "risk_findings.json",
    "test_hooks.json",
    "source_scope.json",
    "evidence_cards.json",
    "black_box_cases.json",
    "impact_scope.json",
    "flow_delta.json",
    "test_recommendations.json",
    "workflow_output_materialization.json",
    "report.md",
  ];
  const rank = order.indexOf(name);
  return rank === -1 ? order.length : rank;
}

export function artifactAudience(
  artifact: WorkbenchTaskArtifact,
): "deliverable" | "input" | "support" | "diagnostic" {
  if (
    artifact.audience === "deliverable" ||
    artifact.audience === "input" ||
    artifact.audience === "support" ||
    artifact.audience === "diagnostic"
  ) {
    return artifact.audience;
  }
  if (artifact.relative_path.startsWith("inputs/")) {
    return "input";
  }
  if (workflowOutputArtifactRank(artifact.relative_path) < 10) {
    return "deliverable";
  }
  return AUDIT_ARTIFACT_KIND_ORDER.includes(artifact.kind)
    ? "diagnostic"
    : "support";
}

export function artifactAudienceLabel(audience: string): string {
  if (audience === "deliverable") return "交付文件";
  if (audience === "input") return "输入材料";
  if (audience === "diagnostic") return "内部诊断";
  return "支撑文件";
}

export function runStatusDisplayLabel(status: string): string {
  const normalized = status.trim().toLowerCase();
  if (!normalized) return "未知";
  if (["已完成", "运行完成"].includes(status)) return "已完成";
  if (["完成但信息不足", "需要复核"].includes(status)) return "需复核";
  if (["进行中", "运行中"].includes(status)) return "进行中";
  if (["失败", "运行失败", "缺少交付文件", "生成失败"].includes(status)) {
    return "失败";
  }
  if (["等待", "待运行", "等待运行"].includes(status)) return "等待";
  if (
    [
      "passed",
      "pass",
      "ok",
      "completed",
      "complete",
      "success",
      "accepted",
      "done",
      "executed",
    ].includes(normalized)
  ) {
    return normalized === "executed" ? "已执行" : "已完成";
  }
  if (normalized === "ready") return "已就绪";
  if (["completed_empty", "needs_review"].includes(normalized)) return "需复核";
  if (normalized === "interrupted") return "失败";
  if (normalized === "invalid") return "无效";
  if (["partial", "partially_completed"].includes(normalized)) {
    return "部分完成";
  }
  if (
    [
      "running",
      "pending",
      "processing",
      "in_progress",
      "queued",
      "prepared",
    ].includes(normalized)
  ) {
    return "进行中";
  }
  if (
    [
      "incomplete",
      "error",
      "failed",
      "failure",
      "degraded",
      "unavailable",
      "missing_config",
    ].includes(normalized)
  ) {
    return "失败";
  }
  if (["waiting", "not_started", "idle"].includes(normalized)) {
    return "等待";
  }
  if (["skipped", "skip"].includes(normalized)) {
    return "已跳过";
  }
  if (["cancelled", "canceled"].includes(normalized)) {
    return "已取消";
  }
  return status;
}

export function providerStatusDisplayLabel(status: string | undefined): string {
  const normalized = String(status ?? "").trim().toLowerCase();
  if (!normalized) return "待探测";
  if (normalized === "workflow_callable") return "工作流可调用";
  if (normalized === "configured") return "已配置";
  if (normalized === "available") return "可用";
  if (normalized === "missing_config") return "缺少配置";
  if (normalized === "unavailable") return "不可用";
  if (normalized === "degraded") return "降级可用";
  return runStatusDisplayLabel(status ?? "");
}

export function providerDisplayLabel(provider: string | undefined): string {
  const normalized = String(provider ?? "").trim().toLowerCase();
  const labels: Record<string, string> = {
    "local-search": "本地源码检索",
    "builtin-llm": "内置模型",
    gitnexus: "GitNexus",
    cgc: "CGC",
    "claude-code": "Claude Code",
    opencode: "OpenCode",
    codex: "Codex",
    nga: "NGA",
    repo: "源码工作区",
  };
  return labels[normalized] ?? String(provider ?? "执行器");
}

export function diagnosticValueText(value: unknown): string {
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map(diagnosticValueText).filter(Boolean).join("、");
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    const parts = [
      "user_message",
      "message",
      "reason",
      "code",
      "status",
      "provider",
      "path",
      "artifact",
      "command",
    ]
      .map((key) => diagnosticValueText(record[key]))
      .filter(Boolean);
    return Array.from(new Set(parts)).join(" · ");
  }
  return "";
}

export function workflowRunSnapshotSummary(run: PreparedWorkbenchTaskRun): {
  workflow: string;
  repo: string;
  inputs: Array<{ label: string; value: string }>;
  outputs: Array<{ label: string; artifact: string }>;
  steps: string;
} {
  const workflow = run.workflow_snapshot ?? {};
  const workflowId = String(workflow.id ?? run.workflow_id ?? "").trim();
  const workflowName = String(workflow.name ?? "").trim();
  const inputSnapshot = run.input_snapshot ?? {};
  const workflowInputs = Array.isArray(workflow.inputs)
    ? workflow.inputs.filter(
        (item): item is Record<string, unknown> =>
          Boolean(item) && typeof item === "object" && !Array.isArray(item),
      )
    : [];
  const workflowOutputs = Array.isArray(workflow.outputs)
    ? workflow.outputs.filter(
        (item): item is Record<string, unknown> =>
          Boolean(item) && typeof item === "object" && !Array.isArray(item),
      )
    : [];
  const inputs =
    workflowInputs.length > 0
      ? workflowInputs
          .map((input) => {
            const id = String(input.id ?? "").trim();
            if (!id || !(id in inputSnapshot)) return null;
            const label = workflowSnapshotItemLabel(input, id);
            const value = snapshotValueText(inputSnapshot[id]);
            return value ? { label, value } : null;
          })
          .filter((item): item is { label: string; value: string } => Boolean(item))
      : Object.entries(inputSnapshot)
          .map(([id, value]) => ({
            label: id,
            value: snapshotValueText(value),
          }))
          .filter((item) => Boolean(item.value));
  const outputs = workflowOutputs
    .map((output) => {
      const artifact = String(output.artifact ?? output.path ?? output.id ?? "").trim();
      if (!artifact) return null;
      return {
        label: workflowSnapshotItemLabel(output, String(output.id ?? artifact)),
        artifact,
      };
    })
    .filter((item): item is { label: string; artifact: string } => Boolean(item));
  const stepCount = Array.isArray(workflow.steps) ? workflow.steps.length : run.agent_runs.length;
  return {
    workflow: workflowName ? `workflow: ${workflowId} · ${workflowName}` : `workflow: ${workflowId}`,
    repo: `workspace: ${run.workspace_id} · repo: ${artifactShortName(run.repo_path)}`,
    inputs,
    outputs,
    steps: `节点: ${stepCount} · Agent: ${run.agent_runs.length}`,
  };
}

export function workflowSnapshotItemLabel(item: Record<string, unknown>, fallback: string): string {
  return String(
    item.name ??
      item.label ??
      item.title ??
      item.role ??
      fallback,
  ).trim() || fallback;
}

export function snapshotValueText(value: unknown): string {
  const text = diagnosticValueText(value).replace(/\s+/g, " ").trim();
  if (!text) return "";
  return text.length > 140 ? `${text.slice(0, 137)}...` : text;
}

export function compactReasonLabel(reason: unknown): string {
  const normalized = diagnosticValueText(reason);
  const lower = normalized.toLowerCase();
  const exactLabels: Record<string, string> = {
    agent_instruction_policy_missing: "Agent 指令策略缺失",
    stdin_redacted_flag_missing: "输入脱敏标记缺失",
    "codetalk_provider_unavailable:gitnexus": "GitNexus 暂不可用",
    "codetalk_provider_unavailable:cgc": "CGC 暂不可用",
    "agent_cli_unavailable:claude-code": "Claude Code 执行器不可用",
    "primary command unavailable; using fallback: claude":
      "主执行器不可用，已尝试备用命令",
  };
  if (exactLabels[normalized]) return exactLabels[normalized];
  if (normalized.startsWith("codetalk_provider_unavailable:")) {
    return `${normalized.split(":")[1] ?? "CodeTalk 工具"} 暂不可用`;
  }
  if (normalized.startsWith("agent_cli_unavailable:")) {
    return `${normalized.split(":")[1] ?? "Agent"} 执行器不可用`;
  }
  if (lower.includes("outofmemoryerror") || lower.includes("heap")) {
    const size = normalized.match(/\d+(?:\.\d+)?\s*(?:gb|mb)/i)?.[0];
    return `内存不足，当前分析对象超过 CPG/Agent 可用堆内存${size ? `（约 ${size}）` : ""}。建议缩小模块范围或改用本地源码流水线。`;
  }
  if (lower.includes("cpg generation") || lower.includes("joern cpg")) {
    return "Joern CPG 构建失败。建议缩小源码路径、降低并发或切换到本地源码分析路径。";
  }
  if (lower.includes("exit code") || lower.includes("退出码")) {
    const code = normalized.match(/(?:exit code|退出码)\D*(\d+)/i)?.[1];
    return `执行器异常退出${code ? `，退出码 ${code}` : ""}。请查看内部诊断确认失败节点和 stderr。`;
  }
  if (lower.includes("command not found") || lower.includes("找不到命令")) {
    return "找不到执行器命令。请在设置中检查 Agent 命令、PATH 或填写完整可执行文件路径。";
  }
  if (
    lower.includes("missing_artifact") ||
    lower.includes("missing artifacts") ||
    lower.includes("artifact file was not produced")
  ) {
    return "Agent 没有生成工作流要求的交付文件。请从失败节点重试，或检查输出契约。";
  }
  if (lower === "artifact_missing") {
    return "声明的验收产物尚未生成。";
  }
  if (lower === "workflow_callable") {
    return "执行器可被工作流调用，但不需要单独的启动探测。";
  }
  if (lower === "artifact_json_unreadable") {
    return "验收产物不是可读取的 JSON。";
  }
  if (lower.includes("schema")) {
    return "结构化产物未通过 Schema 校验。请查看对应 JSON 产物和工作流输出模板。";
  }
  return normalized || "未提供失败原因";
}

export function payloadText(
  payload: Record<string, unknown>,
  keys: string[],
): string {
  for (const key of keys) {
    const value = payload[key];
    if (typeof value === "string" && value.trim()) return value.trim();
    if (typeof value === "number" || typeof value === "boolean") {
      return String(value);
    }
  }
  return "";
}

export function payloadRecord(
  payload: Record<string, unknown>,
  key: string,
): Record<string, unknown> {
  const value = payload[key];
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export function payloadListText(
  payload: Record<string, unknown>,
  keys: string[],
): string {
  for (const key of keys) {
    const value = payload[key];
    const items = Array.isArray(value)
      ? value.map(diagnosticValueText).filter(Boolean)
      : typeof value === "string" && value.trim()
        ? value.split(",").map((item) => item.trim()).filter(Boolean)
        : [];
    if (items.length > 0) {
      return Array.from(new Set(items)).join(", ");
    }
  }
  return "";
}

export function taskRunEventTypeLabel(eventType: string): string {
  const normalized = eventType.trim().toLowerCase();
  const labels: Record<string, string> = {
    queued: "已排队",
    running: "开始运行",
    step_started: "节点开始",
    step_completed: "节点完成",
    step_failed: "节点失败",
    artifact_created: "产物生成",
    artifact: "产物更新",
    tool_use: "调用执行器",
    tool_result: "执行器返回",
    diagnostic: "内部诊断",
    thinking: "执行思路",
    reasoning: "推理过程",
    trace: "执行跟踪",
    completed: "运行完成",
    completed_empty: "完成但信息不足",
    needs_review: "需要复核",
    interrupted: "运行中断",
    cancelled: "已取消",
  };
  return labels[normalized] ?? runStatusDisplayLabel(eventType);
}

export function taskRunEventTitle(event: WorkbenchTaskRunEvent): string {
  const stepId = payloadText(event.payload, ["step_id", "id"]);
  const artifact = payloadText(event.payload, ["artifact", "path", "relative_path"]);
  const base = taskRunEventTypeLabel(event.event_type);
  if ((event.event_type === "artifact_created" || event.event_type === "artifact") && artifact) {
    return `${base} · ${artifactShortName(artifact)}`;
  }
  return stepId ? `${base} · ${stepId}` : base;
}

export function taskRunEventDetail(event: WorkbenchTaskRunEvent): string {
  const payload = event.payload;
  const runtime = payloadRecord(payload, "runtime");
  const userMessage = payloadText(payload, ["user_message", "message"]);
  if (userMessage) return compactReasonLabel(userMessage);
  const error = payloadText(payload, ["error", "reason", "detail"]);
  if (error) return compactReasonLabel(error);
  const executor =
    payloadText(payload, ["provider", "executor", "step_type"]) ||
    payloadText(runtime, ["provider"]);
  const status = payloadText(payload, ["status"]);
  const artifact = payloadText(payload, ["artifact", "path", "relative_path"]);
  const mcpProfile =
    payloadText(payload, ["mcp_profile"]) ||
    payloadText(runtime, ["mcp_profile"]);
  const skills =
    payloadListText(payload, ["skills"]) ||
    payloadListText(runtime, ["skills"]);
  const cwdLabel =
    payloadText(payload, ["cwd_label"]) ||
    payloadText(runtime, ["cwd_label"]);
  const requiredArtifacts =
    payloadListText(payload, ["required_artifacts"]) ||
    payloadListText(runtime, ["required_artifacts"]);
  const parts = [
    executor ? providerDisplayLabel(executor) : "",
    status ? runStatusDisplayLabel(status) : "",
    mcpProfile ? `MCP: ${mcpProfile}` : "",
    skills ? `技能: ${skills}` : "",
    cwdLabel ? `目录: ${cwdLabel}` : "",
    requiredArtifacts ? `产物: ${requiredArtifacts}` : "",
    artifact ? artifactShortName(artifact) : "",
  ].filter(Boolean);
  return parts.join(" · ");
}

export function taskRunEventTone(eventType: string): "danger" | "success" | "primary" | "muted" {
  const normalized = eventType.trim().toLowerCase();
  if (["step_failed", "failed", "error", "interrupted"].includes(normalized)) return "danger";
  if (["step_completed", "artifact_created", "artifact", "tool_result", "completed"].includes(normalized)) {
    return "success";
  }
  if (["running", "step_started", "queued", "tool_use"].includes(normalized)) return "primary";
  return "muted";
}

export function workflowAuditWarningLabel(warning: {
  code?: string;
  path?: string;
  message?: string;
}): string {
  const code = String(warning.code ?? "").trim();
  const path = String(warning.path ?? "").trim();
  const message = String(warning.message ?? "").trim();
  const labels: Record<string, string> = {
    agent_task_missing_required_artifacts:
      "Agent 节点未声明必需交付文件；CodeTalk 仍可运行，但产物验收和证据回放能力会变弱。",
    json_output_missing_schema:
      "JSON 输出缺少 Schema；Agent 产物仍会被保存，但结构化校验能力会受限。建议在输出模板中补充 schema。",
    semantic_import_on_non_test_cases_output:
      "semantic_import 主要用于测试用例输出；该输出导入语义库时可能被拒绝。",
    evidence_memory_on_non_json_output:
      "evidence_memory 主要用于 JSON 输出；CodeTalk 只能从本地校验过的结构化 JSON 产物中固化证据。",
    agent_mcp_input_without_mcp_step:
      "该输入标记为由 Agent MCP 读取，但没有 Agent 节点声明 mcp_profile；Agent CLI 可能无法判断应使用哪个 MCP 凭据配置。",
  };
  const translated = labels[code] ?? compactReasonLabel(message || code);
  return path ? `${translated}（位置：${path}）` : translated;
}

export function acceptanceIssueLabel(issue: Record<string, unknown>): string {
  const id = String(issue.id ?? "");
  const reason = String(issue.reason ?? "");
  if (id.includes("agent_instruction_policy")) {
    return "Agent 指令策略缺失";
  }
  if (id.includes("agent_stdin_redaction")) {
    return "输入脱敏标记缺失";
  }
  if (id.includes("provider_readiness_agent")) {
    const provider = String(issue.provider ?? "执行器");
    const status = String(issue.provider_status ?? issue.reason ?? "");
    if (status === "workflow_callable") {
      return `${provider} 可被工作流调用，无需额外 Agent 启动探测`;
    }
    return `${provider} 执行器未就绪：${compactReasonLabel(status || reason)}`;
  }
  if (reason === "artifact_missing") {
    const path = String(issue.relative_path ?? "");
    return path ? `缺少验收产物：${path}` : "缺少声明的验收产物";
  }
  return compactReasonLabel(reason || id);
}

export function workflowRunResultMessage(
  prefix: string,
  result: {
    status?: string;
    task_run_id?: string;
    evidence_materialization?: { status?: string } | null;
    semantic_output_import?: { status?: string } | null;
    acceptance_audit?: { status?: string } | null;
  },
): string {
  const parts = [
    `${prefix}${runStatusDisplayLabel(String(result.status ?? ""))}`,
    result.task_run_id ? `任务 ${result.task_run_id}` : "",
    `证据固化 ${runStatusDisplayLabel(String(result.evidence_materialization?.status ?? "skipped"))}`,
    `语义导入 ${runStatusDisplayLabel(String(result.semantic_output_import?.status ?? "skipped"))}`,
    `验收 ${runStatusDisplayLabel(String(result.acceptance_audit?.status ?? "skipped"))}`,
  ].filter(Boolean);
  return parts.join(" · ");
}

export function suggestedWorkflowIdFromError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error ?? "");
  return message.match(/"suggested_id"\s*:\s*"([^"]+)"/)?.[1] ?? "";
}

export function workflowHasSpecializedStep(payload: Record<string, unknown>): boolean {
  const steps = Array.isArray(payload.steps)
    ? payload.steps.filter(
        (step): step is Record<string, unknown> =>
          Boolean(step && typeof step === "object" && !Array.isArray(step)),
      )
    : [];
  return steps.some((step) => {
    const stepType = String(step.type ?? "").trim();
    return Boolean(
      stepType &&
        !["agent_task", "evidence_validate", "report_render"].includes(stepType),
    );
  });
}

export function groupArtifactsByAudience(artifacts: WorkbenchTaskArtifact[]) {
  const sortedArtifacts = prioritizedAuditArtifacts(artifacts);
  return {
    deliverable: sortedArtifacts.filter(
      (artifact) => artifactAudience(artifact) === "deliverable",
    ),
    input: sortedArtifacts.filter(
      (artifact) => artifactAudience(artifact) === "input",
    ),
    support: sortedArtifacts.filter(
      (artifact) => artifactAudience(artifact) === "support",
    ),
    diagnostic: sortedArtifacts.filter(
      (artifact) => artifactAudience(artifact) === "diagnostic",
    ),
  };
}

export function Panel({
  title,
  icon,
  children,
}: {
  title: string;
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="ct-workbench-panel ct-reveal ct-liquid-glass min-w-0 rounded-xl p-4">
      <h2 className="ct-workbench-panel-title mb-3 flex items-center gap-2 text-sm font-semibold text-on-surface">
        {icon}
        {title}
      </h2>
      {children}
    </section>
  );
}

export function ProviderFactRow({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="ct-provider-kv-row">
      <span className="ct-provider-kv-label">{label}</span>
      <span className="ct-provider-kv-value">{value}</span>
    </div>
  );
}

export function ProviderSectionTitle({ children }: { children: React.ReactNode }) {
  return <p className="ct-provider-section-title">{children}</p>;
}
