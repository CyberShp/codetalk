"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent, ReactNode } from "react";
import { flushSync } from "react-dom";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  ClipboardList,
  Copy,
  Database,
  Download,
  Library,
  Loader2,
  PlayCircle,
  RefreshCw,
  RotateCcw,
  Save,
  Search,
  MessageSquareText,
  Trash2,
  WandSparkles,
} from "lucide-react";
import { AnimatePresence, motion } from "framer-motion";
import { api } from "@/lib/api";
import type {
  EvidenceMemoryItem,
  EvidenceSourceSlice,
  ExternalAgentStartupProbeResult,
  AgentCommandResolutionDetail,
  AgentRunExecutionResult,
  ArtifactValidationResult,
  MaterializeEvidenceResult,
  MaterializeWorkflowOutputsResult,
  PreparedWorkbenchTaskRun,
  SemanticCase,
  SemanticCaseImportResult,
  TaskRerunExecutionResult,
  TaskRerunHistory,
  TaskRerunPlan,
  TaskRerunPlanValidation,
  WorkbenchDeploymentProbeResult,
  WorkflowDefinition,
  WorkflowExecutionResult,
  WorkflowPreset,
  WorkbenchWorkflowCapabilities,
  WorkbenchAcceptanceAudit,
  WorkbenchProviderCapabilitiesMatrix,
  WorkbenchProviderTaskProbeResult,
  WorkbenchSmokeE2EResult,
  WorkbenchSystemAudit,
  WorkbenchTaskArtifact,
  WorkbenchTaskArtifactContent,
  WorkbenchTaskArtifactManifest,
  Workspace,
  WorkflowDraftServerAudit,
  WorkflowGenerationDraftResult,
} from "@/lib/types";

const MIN_VISIBLE_BUSY_ACTION_MS = 600;
const WORKFLOW_CANVAS_WIDTH = 1500;
const WORKFLOW_CANVAS_HEIGHT = 1100;
const WORKFLOW_NODE_WIDTH = 168;
const WORKFLOW_NODE_HEIGHT = 96;

const DEFAULT_WORKFLOW = {
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

const DEFAULT_INPUTS = {
  mr_link: "https://codehub.example.local/group/project/-/merge_requests/1",
  design_doc: "",
  coverage_report: "",
};

type WorkbenchView = "run" | "workflow" | "knowledge" | "diagnostics";

function WorkbenchStageFrame({
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

const CORE_WORKFLOW_PRESET_IDS = new Set([
  "module_analysis",
  "resource_leak_hunt",
  "mr_blackbox_test",
  "patch_impact_review",
  "source_flow_sfmea_blackbox",
]);

const WORKFLOW_NAME_ZH: Record<string, string> = {
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

function workflowDisplayName(
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

function workflowPresetGroup(
  preset: WorkflowPreset,
): "核心工作流" | "常用测试场景" {
  if (preset.group === "core") return "核心工作流";
  if (preset.group === "common_test_scenario") return "常用测试场景";
  return CORE_WORKFLOW_PRESET_IDS.has(preset.id)
    ? "核心工作流"
    : "常用测试场景";
}

const WORKFLOW_BUILDER_SCENARIOS = {
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

const DEFAULT_BUILDER_OUTPUT_SCHEMAS = {
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

const DEFAULT_BUILDER_EVIDENCE_MAPPINGS = {
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

const DEFAULT_BUILDER_SEMANTIC_IMPORTS = {
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

const DEFAULT_BUILDER_INPUT_SCHEMAS = {
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

const DEFAULT_SEMANTIC_CASE = {
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

const DEFAULT_SEMANTIC_LINES = [
  "TLS handshake fails with invalid credentials -> connection is rejected and resources are released",
  "TLS disabled by configuration -> connection uses the non-TLS path and reports the selected mode",
].join("\n");

function pretty(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function parseJsonObject(value: string): Record<string, unknown> {
  const parsed = JSON.parse(value) as unknown;
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("JSON must be an object");
  }
  return parsed as Record<string, unknown>;
}

function workflowIdFromJson(value: string): string {
  try {
    return String(parseJsonObject(value).id ?? "").trim();
  } catch {
    return "";
  }
}

function parseJsonValue(value: string): unknown {
  return JSON.parse(value) as unknown;
}

function parseCommaSeparated(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function uniqueWorkflowStrings(values: string[]): string[] {
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

function parseWorkflowSpecList(
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

const WORKFLOW_MODULE_PALETTE = [
  {
    id: "input",
    label: "输入模块",
    tone: "border-sky-300/35 bg-sky-400/8 text-sky-700",
  },
  {
    id: "agent",
    label: "智能体模块",
    tone: "border-primary/35 bg-primary/10 text-primary",
  },
  {
    id: "mcp",
    label: "MCP 模块",
    tone: "border-teal-300/35 bg-teal-400/10 text-teal-700",
  },
  {
    id: "skills",
    label: "Skills 模块",
    tone: "border-violet-300/35 bg-violet-400/10 text-violet-700",
  },
  {
    id: "gitnexus",
    label: "GitNexus 模块",
    tone: "border-emerald-300/35 bg-emerald-400/10 text-emerald-700",
  },
  {
    id: "cgc",
    label: "CGC 模块",
    tone: "border-amber-300/35 bg-amber-400/10 text-amber-700",
  },
  {
    id: "output",
    label: "输出模块",
    tone: "border-rose-300/35 bg-rose-400/10 text-rose-700",
  },
];

type WorkflowPaletteModuleId = (typeof WORKFLOW_MODULE_PALETTE)[number]["id"];
type WorkflowCanvasNodeKind = "input" | "context" | "agent" | "output" | "verify";
type WorkflowCanvasNode = {
  id: string;
  kind: WorkflowCanvasNodeKind;
  title: string;
  subtitle: string;
  body: string[];
  x: number;
  y: number;
  source: "contract" | "canvas";
};
type WorkflowCanvasEdge = {
  id: string;
  source: string;
  target: string;
  label?: string;
};
type WorkflowNodePosition = { x: number; y: number };
type WorkflowCanvasLayout = {
  nodes: Array<{
    id: string;
    kind: WorkflowCanvasNodeKind;
    title: string;
    subtitle: string;
    x: number;
    y: number;
    source: "contract" | "canvas";
  }>;
  edges?: WorkflowCanvasEdge[];
  hidden_node_ids: string[];
};

const WORKFLOW_NODE_TONE: Record<WorkflowCanvasNodeKind, string> = {
  input: "border-sky-300/45 bg-sky-400/10",
  context: "border-emerald-300/45 bg-emerald-400/10",
  agent: "border-primary/35 bg-primary/10",
  output: "border-rose-300/45 bg-rose-400/10",
  verify: "border-amber-300/45 bg-amber-400/10",
};

type WorkflowSkillOption = NonNullable<
  WorkbenchWorkflowCapabilities["skill_catalog"]
>[number];

const FALLBACK_WORKFLOW_SKILLS: WorkflowSkillOption[] = [
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

const DEFAULT_BUILDER_SKILL_IDS = FALLBACK_WORKFLOW_SKILLS.filter(
  (skill) => skill.default_enabled,
).map((skill) => skill.id);

function workflowPaletteKind(moduleId: WorkflowPaletteModuleId): WorkflowCanvasNodeKind {
  if (moduleId === "input" || moduleId === "agent" || moduleId === "output") {
    return moduleId;
  }
  return "context";
}

function workflowPaletteSubtitle(moduleId: WorkflowPaletteModuleId): string {
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

function clampWorkflowNodePosition(
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

function isWorkflowCanvasNodeKind(value: unknown): value is WorkflowCanvasNodeKind {
  return (
    value === "input" ||
    value === "context" ||
    value === "agent" ||
    value === "output" ||
    value === "verify"
  );
}

function workflowLayoutFromPayload(payload: unknown): WorkflowCanvasLayout | null {
  if (!payload || typeof payload !== "object") return null;
  const ui = (payload as { ui?: unknown }).ui;
  if (!ui || typeof ui !== "object") return null;
  const layout = (ui as { layout?: unknown }).layout;
  if (!layout || typeof layout !== "object") return null;
  const rawNodes = (layout as { nodes?: unknown }).nodes;
  const rawEdges = (layout as { edges?: unknown }).edges;
  const rawHidden = (layout as { hidden_node_ids?: unknown }).hidden_node_ids;
  const nodes = Array.isArray(rawNodes)
    ? rawNodes
        .map((item) => {
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
          };
        })
        .filter((item): item is WorkflowCanvasLayout["nodes"][number] =>
          Boolean(item),
        )
    : [];
  const hidden_node_ids = Array.isArray(rawHidden)
    ? rawHidden.map((item) => String(item)).filter(Boolean)
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
  return { nodes, edges, hidden_node_ids };
}

function safeWorkflowSpecList(
  value: string,
  defaultType: string,
): Array<{ id: string; type: string; resolver?: string; artifact?: string }> {
  try {
    return parseWorkflowSpecList(value, defaultType);
  } catch {
    return [];
  }
}

function workflowSpecToText(spec: {
  id: string;
  type: string;
  resolver?: string;
  artifact?: string;
}): string {
  const base = `${spec.id}:${spec.type}${spec.resolver ? "@" + spec.resolver : ""}`;
  return spec.artifact ? `${base}=${spec.artifact}` : base;
}

function workflowItemLabel(
  labels: Record<string, string>,
  id: string,
): string {
  return (labels[id] || id).trim();
}

function workflowInputDisplayName(input: Record<string, unknown>): string {
  return String(input.label ?? input.role ?? input.id ?? "输入");
}

function safeArtifactDownloadFilename(relativePath: string): string {
  const filename = relativePath
    .split("/")
    .filter(Boolean)
    .join("__")
    .replace(/[\\/:*?"<>|]+/g, "-")
    .slice(0, 120);
  return filename || "workbench-artifact.txt";
}

function downloadTextFile(filename: string, content: string, type: string) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function outputArtifactForSpec(
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

function outputSchemaForSpec(
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
  const wildcard = allSchemas["*"];
  if (wildcard && typeof wildcard === "object" && !Array.isArray(wildcard)) {
    return wildcard as Record<string, unknown>;
  }
  return null;
}

function outputEvidenceMappingForSpec(
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

function outputSemanticImportForSpec(
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

function inputSchemaForSpec(
  inputId: string,
  inputType: string,
  allSchemas: Record<string, unknown>,
): Record<string, unknown> | null {
  const direct = allSchemas[inputId];
  if (direct && typeof direct === "object" && !Array.isArray(direct)) {
    return direct as Record<string, unknown>;
  }
  const byType = allSchemas[`type:${inputType}`];
  if (byType && typeof byType === "object" && !Array.isArray(byType)) {
    return byType as Record<string, unknown>;
  }
  const wildcard = allSchemas["*"];
  if (wildcard && typeof wildcard === "object" && !Array.isArray(wildcard)) {
    return wildcard as Record<string, unknown>;
  }
  return null;
}

function workflowInputsFromJson(value: string): Array<Record<string, unknown>> {
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

function workflowOutputsFromJson(value: string): Array<Record<string, unknown>> {
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

function workflowStepsFromJson(value: string): Array<Record<string, unknown>> {
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

function workflowOutputDisplayName(output: Record<string, unknown>): string {
  return String(output.label ?? output.id ?? output.artifact ?? "输出");
}

function artifactShortName(path: string): string {
  return path.split("/").filter(Boolean).at(-1) || path;
}

type WorkflowDraftAudit = {
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

function workflowDraftAudit(value: string): WorkflowDraftAudit {
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

function inputTextValue(
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

function updateInputsJsonValue(
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

function isFileLikeWorkflowInput(inputType: string): boolean {
  return ["file", "patch", "diff", "coverage_report"].includes(inputType);
}

function semanticCasesFromLines({
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

function isBulkSemanticImportPayload(value: unknown): boolean {
  if (Array.isArray(value)) return true;
  if (!value || typeof value !== "object") return false;
  const payload = value as Record<string, unknown>;
  return Array.isArray(payload.cases) || Array.isArray(payload.items);
}

function fastContextDecisionSummary(
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

type InputContextFileSummary = {
  inputId: string;
  kind: string;
  filename: string;
  suffix: string;
  chunkCount: number;
  textTruncated: boolean;
  parseWarnings: string[];
};

type InputContextSummary = {
  fileCount: number;
  inputs: InputContextFileSummary[];
};

type AgentMcpRequestSummary = {
  inputId: string;
  inputType: string;
  credentialOwner: string;
  codetalkFetchAllowed: boolean;
  mcpProfiles: string[];
  requiredArtifacts: string[];
};

type ProviderReadinessSummary = {
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

function inputContextSummary(
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

function agentMcpRequestSummary(
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

function providerReadinessSummary(
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

type EvidenceValidationSummary = {
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

type WorkflowOutputMaterializationSummary = {
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

type ReplayPlanSummary = {
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

type ExecutionInputSummary = {
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

type BlackBoxGenerationPolicySummary = {
  termCount: number;
  caseCount: number;
  firstCaseId: string;
  firstTerms: string[];
  allowedUses: string[];
  mustNotUse: string[];
  authorityRule: string;
};

type MemoryArtifactSummary = {
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

type InputMaterialsSummary = {
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

type FailureRetryContextSummary = {
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

function commandResolutionLines(
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

type AcceptanceProviderIssue = {
  provider: string;
  status: string;
  reason: string;
  startupProbeEndpoint: string;
  usedFallback: boolean;
  deploymentTaskProbeStatus: string;
  deploymentProbeId: string;
  deploymentEvidenceConflict: boolean;
};

type AcceptanceWorkflowOutputIssue = {
  outputId: string;
  status: string;
  reason: string;
  artifact: string;
  schemaErrorCount: number;
};

type AcceptanceInstructionPolicyIssue = {
  id: string;
  label: string;
  reason: string;
  relativePath: string;
  expectedFiles: string[];
};

type AcceptanceInputRedactionIssue = {
  id: string;
  label: string;
  reason: string;
  relativePath: string;
  stdinSha: string;
};

function acceptanceProviderIssues(
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

function acceptanceCodetalkProviderIssues(
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

function acceptanceWorkflowOutputIssues(
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

function acceptanceInstructionPolicyIssues(
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

function acceptanceInputRedactionIssues(
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

function evidenceValidationSummary(
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

function workflowOutputMaterializationSummary(
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

function materializationAuditOutputs(
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

function replayPlanSummary(
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

function executionInputSummary(
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

function blackBoxGenerationPolicySummary(
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

function memoryArtifactSummary(
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

function inputMaterialsSummary(
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

function failureRetryContextSummary(
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

function rejectedOutputLabel(item: Record<string, unknown>): string {
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

function rejectedOutputReason(item: Record<string, unknown>): string {
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

function evidenceAuditRefs(provenance: Record<string, unknown>): Array<{
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

const AUDIT_ARTIFACT_KIND_ORDER = [
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

function prioritizedAuditArtifacts(
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

function workflowOutputArtifactRank(relativePath: string): number {
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

function artifactAudience(
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

function artifactAudienceLabel(audience: string): string {
  if (audience === "deliverable") return "交付文件";
  if (audience === "input") return "输入材料";
  if (audience === "diagnostic") return "内部诊断";
  return "支撑文件";
}

function runStatusDisplayLabel(status: string): string {
  const normalized = status.trim().toLowerCase();
  if (!normalized) return "未知";
  if (
    [
      "ready",
      "passed",
      "pass",
      "ok",
      "completed",
      "complete",
      "success",
      "accepted",
      "done",
    ].includes(normalized)
  ) {
    return "已完成";
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
      "invalid",
    ].includes(normalized)
  ) {
    return "失败";
  }
  if (["waiting", "skipped", "not_started", "idle"].includes(normalized)) {
    return "等待";
  }
  if (["cancelled", "canceled"].includes(normalized)) {
    return "已取消";
  }
  return status;
}

function compactReasonLabel(reason: string): string {
  const normalized = reason.trim();
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
  if (lower.includes("missing_artifact") || lower.includes("missing artifacts")) {
    return "Agent 没有生成工作流要求的交付文件。请从失败节点重试，或检查输出契约。";
  }
  if (lower.includes("schema")) {
    return "结构化产物未通过 Schema 校验。请查看对应 JSON 产物和工作流输出模板。";
  }
  return normalized || "未提供失败原因";
}

function acceptanceIssueLabel(issue: Record<string, unknown>): string {
  const id = String(issue.id ?? "");
  const reason = String(issue.reason ?? "");
  if (id.includes("agent_instruction_policy")) {
    return "Agent 指令策略缺失";
  }
  if (id.includes("agent_stdin_redaction")) {
    return "输入脱敏标记缺失";
  }
  return compactReasonLabel(reason || id);
}

function groupArtifactsByAudience(artifacts: WorkbenchTaskArtifact[]) {
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

function Panel({
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

function ProviderFactRow({
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

function ProviderSectionTitle({ children }: { children: React.ReactNode }) {
  return <p className="ct-provider-section-title">{children}</p>;
}

export function AgentWorkbenchExperience({
  initialView = "run",
}: {
  initialView?: WorkbenchView;
}) {
  const router = useRouter();
  const workbenchRootRef = useRef<HTMLDivElement | null>(null);
  const workflowBoardRef = useRef<HTMLDivElement | null>(null);
  const workflowCanvasInnerRef = useRef<HTMLDivElement | null>(null);
  const workspaceAutoSelectionDoneRef = useRef(false);
  const paletteDragModuleRef = useRef<string | null>(null);
  const palettePointerDragRef = useRef<{
    moduleId: string;
    startX: number;
    startY: number;
  } | null>(null);
  const workflowDragRef = useRef<{
    id: string;
    pointerId: number;
    startClientX: number;
    startClientY: number;
    startX: number;
    startY: number;
  } | null>(null);
  const [workflows, setWorkflows] = useState<WorkflowDefinition[]>([]);
  const [workflowPresets, setWorkflowPresets] = useState<WorkflowPreset[]>([]);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workflowJson, setWorkflowJson] = useState(pretty(DEFAULT_WORKFLOW));
  const [aiWorkflowPrompt, setAiWorkflowPrompt] = useState(
    "针对 SPDK iSCSI login 生成灰白盒测试设计工作流：先查 GitNexus/CGC 和源码证据，再输出流程、SFMEA、黑盒用例，并保存可下载产物。",
  );
  const [aiWorkflowPreferredId, setAiWorkflowPreferredId] = useState(
    "iscsi_login_gray_white_test",
  );
  const [aiWorkflowPreferredName, setAiWorkflowPreferredName] = useState(
    "iSCSI Login 灰白盒测试设计",
  );
  const [aiWorkflowGeneration, setAiWorkflowGeneration] =
    useState<WorkflowGenerationDraftResult | null>(null);
  const [builderScenario, setBuilderScenario] =
    useState<keyof typeof WORKFLOW_BUILDER_SCENARIOS>("mr_blackbox_test");
  const [builderWorkflowId, setBuilderWorkflowId] =
    useState("custom_mr_blackbox");
  const [builderWorkflowName, setBuilderWorkflowName] =
    useState("自定义 MR 黑盒测试工作流");
  const [builderInputSpec, setBuilderInputSpec] = useState<string>(
    WORKFLOW_BUILDER_SCENARIOS.mr_blackbox_test.inputs,
  );
  const [builderOutputSpec, setBuilderOutputSpec] = useState<string>(
    WORKFLOW_BUILDER_SCENARIOS.mr_blackbox_test.outputs,
  );
  const [builderProvider, setBuilderProvider] = useState("claude-code");
  const [builderMcpProfile, setBuilderMcpProfile] = useState("codehub-mcp");
  const [builderSkillQuery, setBuilderSkillQuery] = useState("");
  const [builderGoal, setBuilderGoal] = useState<string>(
    WORKFLOW_BUILDER_SCENARIOS.mr_blackbox_test.goal,
  );
  const [builderArtifacts, setBuilderArtifacts] = useState<string>(
    WORKFLOW_BUILDER_SCENARIOS.mr_blackbox_test.artifacts,
  );
  const [builderSkillIds, setBuilderSkillIds] = useState<string[]>(
    DEFAULT_BUILDER_SKILL_IDS,
  );
  const [builderOutputSchemas, setBuilderOutputSchemas] = useState(
    pretty(DEFAULT_BUILDER_OUTPUT_SCHEMAS),
  );
  const [builderEvidenceMappings, setBuilderEvidenceMappings] = useState(
    pretty(DEFAULT_BUILDER_EVIDENCE_MAPPINGS),
  );
  const [builderSemanticImports, setBuilderSemanticImports] = useState(
    pretty(DEFAULT_BUILDER_SEMANTIC_IMPORTS),
  );
  const [builderInputSchemas, setBuilderInputSchemas] = useState(
    pretty(DEFAULT_BUILDER_INPUT_SCHEMAS),
  );
  const [builderInputLabels, setBuilderInputLabels] = useState<
    Record<string, string>
  >({});
  const [builderOutputLabels, setBuilderOutputLabels] = useState<
    Record<string, string>
  >({});
  const [newWorkflowInputName, setNewWorkflowInputName] = useState("");
  const [newWorkflowInputId, setNewWorkflowInputId] = useState("");
  const [newWorkflowInputType, setNewWorkflowInputType] = useState("file");
  const [newWorkflowInputResolver, setNewWorkflowInputResolver] =
    useState("manual");
  const [newWorkflowOutputName, setNewWorkflowOutputName] = useState("");
  const [newWorkflowOutputId, setNewWorkflowOutputId] = useState("");
  const [newWorkflowOutputType, setNewWorkflowOutputType] = useState("json");
  const [newWorkflowOutputArtifact, setNewWorkflowOutputArtifact] =
    useState("");
  const [workflowCanvasEdges, setWorkflowCanvasEdges] = useState<
    WorkflowCanvasEdge[]
  >([]);
  const [workflowLinkSourceId, setWorkflowLinkSourceId] = useState("");
  const [workflowLinkTargetId, setWorkflowLinkTargetId] = useState("");
  const [workflowNodePositions, setWorkflowNodePositions] = useState<
    Record<string, WorkflowNodePosition>
  >({});
  const [workflowExtraNodes, setWorkflowExtraNodes] = useState<
    WorkflowCanvasNode[]
  >([]);
  const [workflowHiddenNodeIds, setWorkflowHiddenNodeIds] = useState<string[]>(
    [],
  );
  const [workflowNodeTitles, setWorkflowNodeTitles] = useState<
    Record<string, string>
  >({});
  const [activeWorkflowNodeId, setActiveWorkflowNodeId] =
    useState<string>("agent-task");
  const [selectedPresetId, setSelectedPresetId] = useState("");
  const [selectedWorkflowId, setSelectedWorkflowId] = useState(
    DEFAULT_WORKFLOW.id,
  );
  const [workspaceId, setWorkspaceId] = useState("manual-workspace");
  const [repoPath, setRepoPath] = useState("");
  const [providerOverride, setProviderOverride] = useState("");
  const [inputsJson, setInputsJson] = useState(pretty(DEFAULT_INPUTS));
  const [semanticJson, setSemanticJson] = useState(
    pretty(DEFAULT_SEMANTIC_CASE),
  );
  const [semanticFeature, setSemanticFeature] = useState("NVMe TCP TLS");
  const [semanticModule, setSemanticModule] = useState("nvmf_tcp");
  const [semanticLines, setSemanticLines] = useState(DEFAULT_SEMANTIC_LINES);
  const [semanticFile, setSemanticFile] = useState<File | null>(null);
  const [semanticQuery, setSemanticQuery] = useState("tls cleanup");
  const [semanticResults, setSemanticResults] = useState<SemanticCase[]>([]);
  const [memoryQuery, setMemoryQuery] = useState("nvme tcp tls");
  const [manualEvidenceSubject, setManualEvidenceSubject] =
    useState("nvmf_tgt_accept");
  const [manualEvidencePath, setManualEvidencePath] =
    useState("lib/nvmf/nvmf.c");
  const [manualEvidenceText, setManualEvidenceText] = useState(
    "SPDK NVMe-oF target accept path evidence for connect-flow black-box validation.",
  );
  const [memoryResults, setMemoryResults] = useState<EvidenceMemoryItem[]>([]);
  const [memorySlices, setMemorySlices] = useState<
    Record<string, EvidenceSourceSlice[]>
  >({});
  const [providerMatrix, setProviderMatrix] =
    useState<WorkbenchProviderCapabilitiesMatrix | null>(null);
  const [workflowCapabilities, setWorkflowCapabilities] =
    useState<WorkbenchWorkflowCapabilities | null>(null);
  const [systemAudit, setSystemAudit] = useState<WorkbenchSystemAudit | null>(
    null,
  );
  const [providerProbeResults, setProviderProbeResults] = useState<
    Record<string, ExternalAgentStartupProbeResult>
  >({});
  const [providerTaskProbeResults, setProviderTaskProbeResults] = useState<
    Record<string, WorkbenchProviderTaskProbeResult>
  >({});
  const [deploymentProbeResult, setDeploymentProbeResult] =
    useState<WorkbenchDeploymentProbeResult | null>(null);
  const [smokeE2EResult, setSmokeE2EResult] =
    useState<WorkbenchSmokeE2EResult | null>(null);
  const [taskRuns, setTaskRuns] = useState<PreparedWorkbenchTaskRun[]>([]);
  const [preparedRun, setPreparedRun] =
    useState<PreparedWorkbenchTaskRun | null>(null);
  const [artifactManifest, setArtifactManifest] =
    useState<WorkbenchTaskArtifactManifest | null>(null);
  const [artifactContent, setArtifactContent] =
    useState<WorkbenchTaskArtifactContent | null>(null);
  const [workflowExecution, setWorkflowExecution] =
    useState<WorkflowExecutionResult | null>(null);
  const [taskRerunPlan, setTaskRerunPlan] = useState<TaskRerunPlan | null>(
    null,
  );
  const [taskRerunPlanValidation, setTaskRerunPlanValidation] =
    useState<TaskRerunPlanValidation | null>(null);
  const [taskRerunExecution, setTaskRerunExecution] =
    useState<TaskRerunExecutionResult | null>(null);
  const [taskRerunHistory, setTaskRerunHistory] =
    useState<TaskRerunHistory | null>(null);
  const [taskAcceptanceAudit, setTaskAcceptanceAudit] =
    useState<WorkbenchAcceptanceAudit | null>(null);
  const [workflowOutputMaterialize, setWorkflowOutputMaterialize] =
    useState<MaterializeWorkflowOutputsResult | null>(null);
  const [workflowDraftServerAudit, setWorkflowDraftServerAudit] =
    useState<WorkflowDraftServerAudit | null>(null);
  const [workflowInputsUpdated, setWorkflowInputsUpdated] = useState(false);
  const [semanticOutputImport, setSemanticOutputImport] =
    useState<SemanticCaseImportResult | null>(null);
  const [executionResults, setExecutionResults] = useState<
    Record<string, AgentRunExecutionResult>
  >({});
  const [validationResults, setValidationResults] = useState<
    Record<string, ArtifactValidationResult>
  >({});
  const [materializeResults, setMaterializeResults] = useState<
    Record<string, MaterializeEvidenceResult>
  >({});
  const [activeWorkbenchView, setActiveWorkbenchView] =
    useState<WorkbenchView>(initialView);
  const [prefersReducedMotion, setPrefersReducedMotion] = useState(false);
  const [motionPreferenceReady, setMotionPreferenceReady] = useState(false);
  const [loading, setLoading] = useState(false);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const busyActionRef = useRef<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [openingConversation, setOpeningConversation] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setActiveWorkbenchView(initialView);
  }, [initialView]);

  const workflowOptions = useMemo(() => {
    const seen = new Set<string>();
    return [
      ...workflows,
      ...workflowPresets
        .map((preset) => preset.definition)
        .filter(
          (definition) =>
            !workflows.some((workflow) => workflow.id === definition.id),
        ),
    ].flatMap((workflow) => {
      if (seen.has(workflow.id)) return [];
      seen.add(workflow.id);
      return [
        {
          id: workflow.id,
          label: workflowDisplayName(workflow),
        },
      ];
    });
  }, [workflowPresets, workflows]);

  const groupedWorkflowPresets = useMemo(
    () =>
      (["核心工作流", "常用测试场景"] as const)
        .map((group) => ({
          group,
          items: workflowPresets.filter(
            (preset) => workflowPresetGroup(preset) === group,
          ),
        }))
        .filter((group) => group.items.length > 0),
    [workflowPresets],
  );

  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const updatePreference = () => setPrefersReducedMotion(query.matches);
    updatePreference();
    setMotionPreferenceReady(true);
    query.addEventListener("change", updatePreference);
    return () => query.removeEventListener("change", updatePreference);
  }, []);
  useEffect(() => {
    if (
      selectedWorkflowId !== DEFAULT_WORKFLOW.id ||
      workflowPresets.length === 0
    )
      return;
    const preferredPreset =
      workflowPresets.find((preset) => preset.id === "module_analysis") ??
      workflowPresets[0];
    setSelectedWorkflowId(preferredPreset.definition.id);
    setWorkflowJson((currentJson) => {
      const currentId = workflowIdFromJson(currentJson);
      if (currentId && currentId !== DEFAULT_WORKFLOW.id) return currentJson;
      return pretty(preferredPreset.definition);
    });
  }, [selectedWorkflowId, workflowPresets]);
  const builderProviderOptions = useMemo(() => {
    const providers = (providerMatrix?.providers ?? [])
      .filter((provider) => provider.agent_owned || provider.command.length > 0)
      .map((provider) => ({
        id: provider.provider,
        label: provider.display_name || provider.provider,
        status: provider.status,
      }));
    if (!providers.some((provider) => provider.id === "claude-code")) {
      providers.unshift({
        id: "claude-code",
        label: "Claude Code",
        status: "configured",
      });
    }
    return providers;
  }, [providerMatrix]);
  const workflowSkillOptions = useMemo<WorkflowSkillOption[]>(() => {
    const catalog = workflowCapabilities?.skill_catalog ?? [];
    const merged = [...catalog, ...FALLBACK_WORKFLOW_SKILLS];
    const seen = new Set<string>();
    return merged.filter((skill) => {
      if (!skill.id || seen.has(skill.id)) return false;
      seen.add(skill.id);
      return true;
    });
  }, [workflowCapabilities]);
  const selectedBuilderSkillOptions = useMemo(
    () =>
      workflowSkillOptions.filter((skill) =>
        builderSkillIds.includes(skill.id),
      ),
    [builderSkillIds, workflowSkillOptions],
  );
  const visibleBuilderSkillOptions = useMemo(() => {
    const query = builderSkillQuery.trim().toLowerCase();
    const matches = query
      ? workflowSkillOptions.filter((skill) =>
          [
            skill.id,
            skill.label,
            skill.description ?? "",
            skill.source ?? "",
          ]
            .join(" ")
            .toLowerCase()
            .includes(query),
        )
      : workflowSkillOptions.filter(
          (skill, index) => builderSkillIds.includes(skill.id) || index < 8,
        );
    return matches.slice(0, 24);
  }, [builderSkillIds, builderSkillQuery, workflowSkillOptions]);
  const builderProviderItem = useMemo(
    () =>
      (providerMatrix?.providers ?? []).find(
        (provider) => provider.provider === builderProvider,
      ) ?? null,
    [builderProvider, providerMatrix],
  );
  const builderMcpOptions = useMemo(() => {
    const profiles = uniqueWorkflowStrings([
      ...((builderProviderItem?.capabilities?.mcp_profiles ?? []) as string[]),
      ...(providerMatrix?.providers ?? []).flatMap((provider) =>
        (provider.capabilities?.mcp_profiles ?? []).map(String),
      ),
      "gitnexus",
      "cgc",
      "codehub-mcp",
      "codehub-readonly",
      builderMcpProfile,
    ]);
    return profiles;
  }, [builderMcpProfile, builderProviderItem, providerMatrix]);
  const builderMcpCompatibility = useMemo(() => {
    const profile = builderMcpProfile.trim();
    if (!profile) {
      return {
        level: "muted",
        label: "未启用 MCP",
        detail: "Agent 会使用工作区输入和 CodeTalk 预取上下文。",
      };
    }
    if (!builderProvider.trim()) {
      return {
        level: "pending",
        label: "等待选择执行器",
        detail: "MCP 需求会保留，选择执行器后再校验兼容性。",
      };
    }
    if (!builderProviderItem) {
      return {
        level: "pending",
        label: "自定义执行器待探测",
        detail: "保存和运行前会通过执行器探测确认 MCP 能力。",
      };
    }
    const capabilities = builderProviderItem.capabilities;
    const mcpProfiles = (capabilities?.mcp_profiles ?? []).map(String);
    if (mcpProfiles.includes(profile)) {
      return {
        level: "ok",
        label: "Agent 可直接使用 MCP",
        detail: `${builderProviderItem.display_name || builderProvider} 声明了 ${profile}。`,
      };
    }
    if (capabilities?.supports_mcp && mcpProfiles.length === 0) {
      return {
        level: "pending",
        label: "Agent 支持 MCP，profile 待确认",
        detail: "该执行器支持 MCP 但未声明 profile，建议先运行探测。",
      };
    }
    if (
      profile === "gitnexus" ||
      profile === "cgc" ||
      profile === "codehub-mcp"
    ) {
      return {
        level: "fallback",
        label: "CodeTalk 预取后注入",
        detail: "当前执行器未声明该 MCP；CodeTalk 会优先查源码/GitNexus/CGC 后把证据交给 Agent。",
      };
    }
    return {
      level: "warn",
      label: "MCP 与执行器不匹配",
      detail: "当前执行器未声明该 MCP profile，运行前需要更换执行器或改成 CodeTalk 预取。",
    };
  }, [builderMcpProfile, builderProvider, builderProviderItem]);
  const semanticImportOutputIds = useMemo(
    () =>
      (workflowExecution?.outputs ?? [])
        .filter((output) => {
          const outputId = String(output.id ?? "").toLowerCase();
          const outputType = String(output.type ?? "").toLowerCase();
          const artifact = String(
            output.artifact ?? output.path ?? "",
          ).toLowerCase();
          return (
            output.status === "ok" &&
            (outputType === "test_cases" ||
              outputId === "black_box_cases" ||
              outputId === "test_cases" ||
              artifact.endsWith("black_box_cases.json") ||
              artifact.endsWith("test_cases.json"))
          );
        })
        .map((output) => String(output.id ?? "").trim())
        .filter(Boolean),
    [workflowExecution],
  );
  const parsedPrepareInputs = useMemo(() => {
    try {
      return parseJsonObject(inputsJson || "{}");
    } catch {
      return {};
    }
  }, [inputsJson]);
  const selectedWorkflowInputs = useMemo(() => {
    const registered = workflows.find(
      (workflow) => workflow.id === selectedWorkflowId,
    );
    if (registered?.inputs?.length) return registered.inputs;
    const preset = workflowPresets.find(
      (item) => item.definition.id === selectedWorkflowId,
    );
    if (preset?.definition.inputs?.length) return preset.definition.inputs;
    return workflowInputsFromJson(workflowJson);
  }, [selectedWorkflowId, workflowJson, workflowPresets, workflows]);
  const selectedWorkflowOutputs = useMemo(() => {
    const registered = workflows.find(
      (workflow) => workflow.id === selectedWorkflowId,
    );
    if (registered?.outputs?.length) return registered.outputs;
    const preset = workflowPresets.find(
      (item) => item.definition.id === selectedWorkflowId,
    );
    if (preset?.definition.outputs?.length) return preset.definition.outputs;
    return workflowOutputsFromJson(workflowJson);
  }, [selectedWorkflowId, workflowJson, workflowPresets, workflows]);
  const selectedWorkflowSteps = useMemo(() => {
    const registered = workflows.find(
      (workflow) => workflow.id === selectedWorkflowId,
    );
    if (registered?.steps?.length) return registered.steps;
    const preset = workflowPresets.find(
      (item) => item.definition.id === selectedWorkflowId,
    );
    if (preset?.definition.steps?.length) return preset.definition.steps;
    return workflowStepsFromJson(workflowJson);
  }, [selectedWorkflowId, workflowJson, workflowPresets, workflows]);
  const selectedAgentStep = useMemo(
    () =>
      selectedWorkflowSteps.find(
        (step) => String(step.type ?? "") === "agent_task",
      ) ?? null,
    [selectedWorkflowSteps],
  );
  const selectedAgentSkillInstructions = useMemo(() => {
    const raw = selectedAgentStep?.skill_instructions;
    if (!Array.isArray(raw)) return [];
    return raw
      .map((item) =>
        item && typeof item === "object"
          ? (item as Record<string, unknown>)
          : null,
      )
      .filter((item): item is Record<string, unknown> => Boolean(item));
  }, [selectedAgentStep]);
  const selectedAgentSkillIds = useMemo(() => {
    const raw = selectedAgentStep?.skills;
    return Array.isArray(raw)
      ? raw.map((item) => String(item)).filter(Boolean)
      : [];
  }, [selectedAgentStep]);
  const selectedRunProvider = useMemo(
    () =>
      providerOverride.trim() ||
      String(selectedAgentStep?.provider ?? (builderProvider || "claude-code")),
    [builderProvider, providerOverride, selectedAgentStep],
  );
  const selectedRunMcpProfile = useMemo(
    () =>
      String(
        selectedAgentStep?.mcp_profile ??
          (builderMcpProfile.trim() ? builderMcpProfile : "未启用"),
      ),
    [builderMcpProfile, selectedAgentStep],
  );
  const selectedProviderCapability = useMemo(
    () =>
      (providerMatrix?.providers ?? []).find(
        (provider) => provider.provider === selectedRunProvider,
      ) ?? null,
    [providerMatrix, selectedRunProvider],
  );
  const requiredInputCount = useMemo(
    () =>
      selectedWorkflowInputs.filter((input) => input.required === true).length,
    [selectedWorkflowInputs],
  );
  const filledInputCount = useMemo(
    () =>
      selectedWorkflowInputs.filter((input) => {
        const id = String(input.id ?? "");
        if (!id) return false;
        const value = parsedPrepareInputs[id];
        if (value === null || value === undefined) return false;
        if (typeof value === "string") return value.trim().length > 0;
        if (Array.isArray(value)) return value.length > 0;
        if (typeof value === "object") {
          return Object.keys(value as Record<string, unknown>).length > 0;
        }
        return true;
      }).length,
    [parsedPrepareInputs, selectedWorkflowInputs],
  );
  const preparedProviderReadiness = useMemo(
    () =>
      preparedRun?.task_bundle &&
      typeof preparedRun.task_bundle === "object" &&
      !Array.isArray(preparedRun.task_bundle)
        ? providerReadinessSummary(preparedRun.task_bundle)
        : null,
    [preparedRun],
  );
  const runPhaseCards = useMemo(
    () => [
      {
        label: "准备上下文",
        status: preparedRun ? "完成" : "等待",
        detail: repoPath.trim() ? `源码路径: ${repoPath}` : "等待选择源码路径",
      },
      {
        label: "执行 Agent",
        status: workflowExecution
          ? runStatusDisplayLabel(workflowExecution.status)
          : preparedRun
            ? "等待"
            : "等待",
        detail: `${selectedRunProvider} · ${selectedRunMcpProfile}`,
      },
      {
        label: "校验证据",
        status: taskAcceptanceAudit
          ? runStatusDisplayLabel(taskAcceptanceAudit.status)
          : workflowExecution?.evidence_materialization?.status ??
            (preparedRun ? "待审计" : "等待"),
        detail: taskAcceptanceAudit
          ? `缺少 ${taskAcceptanceAudit.summary.missing_required} 个必需验收项`
          : "等待校验 schema、证据和脱敏",
      },
      {
        label: "固化交付物",
        status: runStatusDisplayLabel(
          workflowOutputMaterialize?.status ??
            workflowExecution?.evidence_materialization?.status ??
            (artifactManifest ? "ready" : "waiting"),
        ),
        detail: `${artifactManifest?.artifacts.length ?? 0} 个产物`,
      },
    ],
    [
      artifactManifest,
      preparedRun,
      repoPath,
      selectedRunMcpProfile,
      selectedRunProvider,
      taskAcceptanceAudit,
      workflowExecution,
      workflowOutputMaterialize,
    ],
  );
  const artifactAudienceGroups = useMemo(
    () => groupArtifactsByAudience(artifactManifest?.artifacts ?? []),
    [artifactManifest],
  );
  const runPanelStatus = useMemo(() => {
    if (!preparedRun) return "空";
    if (
      (taskAcceptanceAudit?.summary.missing_required ?? 0) > 0 ||
      ["incomplete", "error", "failed", "failure"].includes(
        String(taskAcceptanceAudit?.status ?? "").toLowerCase(),
      )
    ) {
      return "失败";
    }
    if (
      workflowOutputMaterialize?.status ||
      ["ready", "passed", "ok", "completed", "success"].includes(
        String(workflowExecution?.status ?? "").toLowerCase(),
      )
    ) {
      return "已完成";
    }
    return "进行中";
  }, [
    preparedRun,
    taskAcceptanceAudit,
    workflowExecution,
    workflowOutputMaterialize,
  ]);
  const runPanelFailureReasons = useMemo(() => {
    const reasons: string[] = [];
    const missingRequired = taskAcceptanceAudit?.summary.missing_required ?? 0;
    if (missingRequired > 0) {
      reasons.push(`缺少 ${missingRequired} 个必需验收项`);
    }
    taskAcceptanceAudit?.missing_required
      .map((issue) => acceptanceIssueLabel(issue))
      .filter(Boolean)
      .forEach((reason) => reasons.push(reason));
    preparedProviderReadiness?.blockingReasons
      .map(compactReasonLabel)
      .forEach((reason) => reasons.push(reason));
    if (!taskAcceptanceAudit && preparedProviderReadiness?.warnings.length) {
      preparedProviderReadiness.warnings
        .slice(0, 3)
        .map(compactReasonLabel)
        .forEach((reason) => reasons.push(reason));
    }
    return Array.from(new Set(reasons)).slice(0, 5);
  }, [preparedProviderReadiness, taskAcceptanceAudit]);
  const runPanelProgress = useMemo(() => {
    const completed = runPhaseCards.filter(
      (phase) => runStatusDisplayLabel(phase.status) === "已完成",
    ).length;
    const failedIndex = runPhaseCards.findIndex(
      (phase) => runStatusDisplayLabel(phase.status) === "失败",
    );
    const runningIndex = runPhaseCards.findIndex(
      (phase) => runStatusDisplayLabel(phase.status) === "进行中",
    );
    const currentIndex =
      failedIndex >= 0
        ? failedIndex
        : runningIndex >= 0
          ? runningIndex
          : Math.min(completed, runPhaseCards.length - 1);
    const total = Math.max(runPhaseCards.length, 1);
    return {
      completed,
      currentIndex,
      percent: Math.round((completed / total) * 100),
      total,
    };
  }, [runPhaseCards]);
  const visibleDeliveryArtifacts = useMemo(() => {
    const manifestArtifacts = artifactManifest?.artifacts ?? [];
    const outputArtifacts = new Set(
      selectedWorkflowOutputs
        .map((output) => String(output.artifact ?? "").trim())
        .filter(Boolean),
    );
    return manifestArtifacts
      .filter((artifact) => {
        if (artifact.kind === "task_bundle") return true;
        if (artifact.kind === "workflow_output_materialization") return true;
        if (artifact.kind === "evidence_validation") return true;
        if (artifact.kind === "semantic_import_outputs") return true;
        if (artifact.relative_path.startsWith("agent_runs/")) return false;
        return outputArtifacts.has(artifact.relative_path);
      })
      .slice(0, 8);
  }, [artifactManifest, selectedWorkflowOutputs]);
  const selectedWorkflowAudit = useMemo(
    () =>
      workflows.find((workflow) => workflow.id === selectedWorkflowId)?.audit,
    [selectedWorkflowId, workflows],
  );
  const workflowDraftAuditSummary = useMemo(
    () => workflowDraftAudit(workflowJson),
    [workflowJson],
  );
  const builderInputItems = useMemo(
    () => safeWorkflowSpecList(builderInputSpec, "free_text"),
    [builderInputSpec],
  );
  const builderOutputItems = useMemo(
    () => safeWorkflowSpecList(builderOutputSpec, "json"),
    [builderOutputSpec],
  );
  const builderOutputPreview = useMemo(() => {
    try {
      const requiredArtifacts = parseCommaSeparated(builderArtifacts);
      const outputSchemas = parseJsonObject(builderOutputSchemas || "{}");
      const evidenceMappings = parseJsonObject(builderEvidenceMappings || "{}");
      const semanticImports = parseJsonObject(builderSemanticImports || "{}");
      return parseWorkflowSpecList(builderOutputSpec, "json").map((output) => {
        const artifact =
          output.artifact ||
          outputArtifactForSpec(output.id, output.type, requiredArtifacts);
        const schema =
          output.type === "json"
            ? outputSchemaForSpec(output.id, outputSchemas)
            : null;
        const evidenceMemory =
          output.type === "json" || output.type === "scope_report"
            ? outputEvidenceMappingForSpec(output.id, evidenceMappings)
            : null;
        const semanticImport =
          output.type === "test_cases"
            ? outputSemanticImportForSpec(
                output.id,
                output.type,
                semanticImports,
              )
            : null;
        return {
          id: output.id,
          type: output.type,
          artifact,
          schema: Boolean(schema),
          evidenceMemory: Boolean(evidenceMemory),
          evidenceKind: evidenceMemory ? String(evidenceMemory.kind ?? "") : "",
          semanticImport: Boolean(semanticImport),
        };
      });
    } catch {
      return [];
    }
  }, [
    builderArtifacts,
    builderEvidenceMappings,
    builderOutputSchemas,
    builderOutputSpec,
    builderSemanticImports,
  ]);
  const workflowContractNodes = useMemo<WorkflowCanvasNode[]>(() => {
    const requiredArtifacts = parseCommaSeparated(builderArtifacts);
    return [
      {
        id: "inputs",
        kind: "input",
        title: "输入",
        subtitle: builderInputItems.length
          ? `${builderInputItems.length} 个入口`
          : "等待输入定义",
        body: builderInputItems
          .slice(0, 4)
          .map(
            (item) =>
              `${workflowItemLabel(builderInputLabels, item.id)}:${item.type}`,
          ),
        x: 36,
        y: 72,
        source: "contract",
      },
      {
        id: "source-context",
        kind: "context",
        title: "源码上下文",
        subtitle: "GitNexus / CGC",
        body: ["优先读取工作区源码", "复用索引与调用图产物"],
        x: 300,
        y: 220,
        source: "contract",
      },
      {
        id: "skills-mcp",
        kind: "context",
        title: "Skills / MCP",
        subtitle: `${selectedBuilderSkillOptions.length} skills · ${builderMcpCompatibility.label}`,
        body: [
          ...selectedBuilderSkillOptions
            .slice(0, 3)
            .map((skill) => skill.label),
          builderMcpProfile ? `MCP: ${builderMcpProfile}` : "MCP: 未启用",
        ],
        x: 565,
        y: 88,
        source: "contract",
      },
      {
        id: "agent-task",
        kind: "agent",
        title: builderProvider || "智能体",
        subtitle: builderMcpProfile
          ? `MCP: ${builderMcpProfile}`
          : "无 MCP 配置",
        body: [builderScenario, builderGoal.trim().slice(0, 72) || "等待目标"],
        x: 840,
        y: 295,
        source: "contract",
      },
      {
        id: "outputs",
        kind: "output",
        title: "输出",
        subtitle: builderOutputItems.length
          ? `${builderOutputItems.length} 个契约`
          : "等待输出定义",
        body: [
          ...builderOutputItems
            .slice(0, 3)
            .map((item) =>
              item.artifact
                ? `${workflowItemLabel(builderOutputLabels, item.id)} -> ${item.artifact}`
                : `${workflowItemLabel(builderOutputLabels, item.id)}:${item.type}`,
          ),
          "sfmea / black_box_cases",
        ],
        x: 1120,
        y: 155,
        source: "contract",
      },
      {
        id: "validation",
        kind: "verify",
        title: "验收",
        subtitle: `${requiredArtifacts.length} 个必需产物`,
        body: [
          `schema:${workflowDraftAuditSummary.warnings.length === 0 ? "ready" : "check"}`,
          `evidence:${workflowDraftAuditSummary.evidenceMemoryOutputCount}`,
          `semantic:${workflowDraftAuditSummary.semanticImportOutputCount}`,
        ],
        x: 1260,
        y: 500,
        source: "contract",
      },
    ];
  }, [
    builderArtifacts,
    builderGoal,
    builderInputItems,
    builderInputLabels,
    builderMcpProfile,
    builderOutputItems,
    builderOutputLabels,
    builderProvider,
    builderScenario,
    builderMcpCompatibility.label,
    selectedBuilderSkillOptions,
    workflowDraftAuditSummary.evidenceMemoryOutputCount,
    workflowDraftAuditSummary.semanticImportOutputCount,
    workflowDraftAuditSummary.warnings.length,
  ]);
  const workflowCanvasNodes = useMemo<WorkflowCanvasNode[]>(
    () =>
      [...workflowContractNodes, ...workflowExtraNodes]
        .filter((node) => !workflowHiddenNodeIds.includes(node.id))
        .map((node) => {
          const override = workflowNodePositions[node.id];
          const title = workflowNodeTitles[node.id];
          return {
            ...node,
            ...(override ? override : {}),
            ...(title ? { title } : {}),
          };
        }),
    [
      workflowContractNodes,
      workflowExtraNodes,
      workflowHiddenNodeIds,
      workflowNodePositions,
      workflowNodeTitles,
    ],
  );
  const defaultWorkflowCanvasEdges = useMemo<WorkflowCanvasEdge[]>(
    () => [
      {
        id: "edge-inputs-source-context",
        source: "inputs",
        target: "source-context",
        label: "源码/文件",
      },
      {
        id: "edge-source-context-agent",
        source: "source-context",
        target: "agent-task",
        label: "证据",
      },
      {
        id: "edge-skills-agent",
        source: "skills-mcp",
        target: "agent-task",
        label: "MCP/Skills",
      },
      {
        id: "edge-agent-outputs",
        source: "agent-task",
        target: "outputs",
        label: "产物",
      },
      {
        id: "edge-outputs-validation",
        source: "outputs",
        target: "validation",
        label: "验收",
      },
    ],
    [],
  );
  const visibleWorkflowCanvasEdges = useMemo(() => {
    const visibleNodeIds = new Set(workflowCanvasNodes.map((node) => node.id));
    return [...defaultWorkflowCanvasEdges, ...workflowCanvasEdges].filter(
      (edge) => visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target),
    );
  }, [defaultWorkflowCanvasEdges, workflowCanvasEdges, workflowCanvasNodes]);
  const activeWorkflowNode = useMemo(
    () =>
      workflowCanvasNodes.find((node) => node.id === activeWorkflowNodeId) ??
      workflowCanvasNodes[0],
    [activeWorkflowNodeId, workflowCanvasNodes],
  );

  function workflowLayoutSnapshot(
    nodes: WorkflowCanvasNode[] = workflowCanvasNodes,
    hiddenNodeIds: string[] = workflowHiddenNodeIds,
    edges: WorkflowCanvasEdge[] = workflowCanvasEdges,
  ): WorkflowCanvasLayout {
    return {
      nodes: nodes.map((node) => ({
        id: node.id,
        kind: node.kind,
        title: node.title,
        subtitle: node.subtitle,
        x: node.x,
        y: node.y,
        source: node.source,
      })),
      edges,
      hidden_node_ids: hiddenNodeIds,
    };
  }

  function mergeWorkflowLayoutIntoJson(
    layout: WorkflowCanvasLayout = workflowLayoutSnapshot(),
  ) {
    setWorkflowJson((current) => {
      try {
        const payload = parseJsonObject(current || "{}");
        const ui =
          payload.ui && typeof payload.ui === "object"
            ? (payload.ui as Record<string, unknown>)
            : {};
        return pretty({
          ...payload,
          ui: {
            ...ui,
            layout,
          },
        });
      } catch {
        return current;
      }
    });
  }

  function applyWorkflowLayout(payload: unknown) {
    const layout = workflowLayoutFromPayload(payload);
    if (!layout) {
      setWorkflowNodePositions({});
      setWorkflowExtraNodes([]);
      setWorkflowHiddenNodeIds([]);
      setWorkflowNodeTitles({});
      setWorkflowCanvasEdges([]);
      return;
    }
    const positions: Record<string, WorkflowNodePosition> = {};
    const titles: Record<string, string> = {};
    const extras: WorkflowCanvasNode[] = [];
    for (const node of layout.nodes) {
      positions[node.id] = clampWorkflowNodePosition({ x: node.x, y: node.y });
      titles[node.id] = node.title;
      if (node.source === "canvas") {
        extras.push({
          id: node.id,
          kind: node.kind,
          title: node.title,
          subtitle: node.subtitle,
          body: ["画布恢复节点", "来自 workflow ui.layout"],
          x: node.x,
          y: node.y,
          source: "canvas",
        });
      }
    }
    setWorkflowNodePositions(positions);
    setWorkflowNodeTitles(titles);
    setWorkflowCanvasEdges(layout.edges ?? []);
    setWorkflowExtraNodes(extras);
    setWorkflowHiddenNodeIds(layout.hidden_node_ids);
    const firstVisible = layout.nodes.find(
      (node) => !layout.hidden_node_ids.includes(node.id),
    );
    if (firstVisible) setActiveWorkflowNodeId(firstVisible.id);
  }

  function startWorkflowNodeDrag(
    event: ReactPointerEvent<HTMLElement>,
    node: WorkflowCanvasNode,
  ) {
    if (event.button !== 0) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    setActiveWorkflowNodeId(node.id);
    workflowDragRef.current = {
      id: node.id,
      pointerId: event.pointerId,
      startClientX: event.clientX,
      startClientY: event.clientY,
      startX: node.x,
      startY: node.y,
    };
  }

  function moveWorkflowNode(event: ReactPointerEvent<HTMLElement>) {
    const drag = workflowDragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    event.preventDefault();
    const nextPosition = clampWorkflowNodePosition({
      x: drag.startX + event.clientX - drag.startClientX,
      y: drag.startY + event.clientY - drag.startClientY,
    });
    setWorkflowNodePositions((current) => ({
      ...current,
      [drag.id]: nextPosition,
    }));
  }

  function endWorkflowNodeDrag(event: ReactPointerEvent<HTMLElement>) {
    const drag = workflowDragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    workflowDragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    const node = workflowCanvasNodes.find((item) => item.id === drag.id);
    if (node) {
      setMessage(`节点位置已更新: ${node.title}`);
      window.setTimeout(() => mergeWorkflowLayoutIntoJson(), 0);
    }
  }

  function appendCommaSpec(current: string, item: string): string {
    const items = parseCommaSeparated(current);
    if (items.includes(item)) return current;
    return [...items, item].join(", ");
  }

  function addBuilderInputContract() {
    const id = newWorkflowInputId.trim();
    const label = newWorkflowInputName.trim() || id;
    const type = newWorkflowInputType.trim() || "free_text";
    const resolver =
      newWorkflowInputResolver && newWorkflowInputResolver !== "manual"
        ? newWorkflowInputResolver
        : "";
    if (!id) {
      setMessage("请输入输入契约 ID");
      return;
    }
    const spec = workflowSpecToText({ id, type, resolver });
    const nextInputSpec = appendCommaSpec(builderInputSpec, spec);
    const nextInputLabels = { ...builderInputLabels, [id]: label };
    flushSync(() => {
      setBuilderInputSpec(nextInputSpec);
      setBuilderInputLabels(nextInputLabels);
      setNewWorkflowInputName("");
      setNewWorkflowInputId("");
    });
    setMessage(`输入契约已添加: ${label}`);
    window.setTimeout(() => {
      try {
        generateWorkflowFromBuilder({
          inputSpec: nextInputSpec,
          inputLabels: nextInputLabels,
        });
      } catch (error) {
        mergeWorkflowLayoutIntoJson();
        setMessage(
          error instanceof Error
            ? `输入契约已添加，但草稿同步失败: ${error.message}`
            : `输入契约已添加，但草稿同步失败`,
        );
      }
    }, 0);
  }

  function addBuilderOutputContract() {
    const id = newWorkflowOutputId.trim();
    const label = newWorkflowOutputName.trim() || id;
    const type = newWorkflowOutputType.trim() || "json";
    const artifact =
      newWorkflowOutputArtifact.trim() ||
      outputArtifactForSpec(id, type, parseCommaSeparated(builderArtifacts));
    if (!id) {
      setMessage("请输入输出契约 ID");
      return;
    }
    const spec = workflowSpecToText({ id, type, artifact });
    const nextOutputSpec = appendCommaSpec(builderOutputSpec, spec);
    const nextArtifacts = appendCommaSpec(builderArtifacts, artifact);
    const nextOutputLabels = { ...builderOutputLabels, [id]: label };
    flushSync(() => {
      setBuilderOutputSpec(nextOutputSpec);
      setBuilderArtifacts(nextArtifacts);
      setBuilderOutputLabels(nextOutputLabels);
      setNewWorkflowOutputName("");
      setNewWorkflowOutputId("");
      setNewWorkflowOutputArtifact("");
    });
    setMessage(`输出契约已添加: ${label}`);
    window.setTimeout(() => {
      try {
        generateWorkflowFromBuilder({
          outputSpec: nextOutputSpec,
          artifacts: nextArtifacts,
          outputLabels: nextOutputLabels,
        });
      } catch (error) {
        mergeWorkflowLayoutIntoJson();
        setMessage(
          error instanceof Error
            ? `输出契约已添加，但草稿同步失败: ${error.message}`
            : `输出契约已添加，但草稿同步失败`,
        );
      }
    }, 0);
  }

  function connectActiveWorkflowNode() {
    const source =
      workflowCanvasNodes.find((node) => node.id === workflowLinkSourceId) ??
      activeWorkflowNode;
    if (!source || !workflowLinkTargetId) return;
    if (source.id === workflowLinkTargetId) {
      setMessage("不能连接到当前节点自身");
      return;
    }
    const target = workflowCanvasNodes.find(
      (node) => node.id === workflowLinkTargetId,
    );
    if (!target) return;
    const exists = workflowCanvasEdges.some(
      (edge) =>
        edge.source === source.id && edge.target === target.id,
    );
    if (exists) {
      setMessage("这两个节点已经连线");
      return;
    }
    const nextEdges = [
      ...workflowCanvasEdges,
      {
        id: `edge-${source.id}-${target.id}-${Date.now().toString(36)}`,
        source: source.id,
        target: target.id,
        label: `${source.title} -> ${target.title}`,
      },
    ];
    setWorkflowCanvasEdges(nextEdges);
    setActiveWorkflowNodeId(source.id);
    setMessage(`连线已添加: ${source.title} -> ${target.title}`);
    mergeWorkflowLayoutIntoJson(
      workflowLayoutSnapshot(workflowCanvasNodes, workflowHiddenNodeIds, nextEdges),
    );
  }

  function addPaletteNodeToCanvas(
    moduleId: string,
    clientX: number,
    clientY: number,
  ) {
    const paletteModule = WORKFLOW_MODULE_PALETTE.find(
      (item) => item.id === moduleId,
    );
    const canvasRect = workflowCanvasInnerRef.current?.getBoundingClientRect();
    if (!paletteModule || !canvasRect) return;
    const position = clampWorkflowNodePosition({
      x: clientX - canvasRect.left - WORKFLOW_NODE_WIDTH / 2,
      y: clientY - canvasRect.top - WORKFLOW_NODE_HEIGHT / 2,
    });
    const nodeId = `canvas-${moduleId}-${Date.now().toString(36)}`;
    const contractId = nodeId.replace(/-/g, "_");
    const node: WorkflowCanvasNode = {
      id: nodeId,
      kind: workflowPaletteKind(paletteModule.id),
      title: paletteModule.label,
      subtitle: workflowPaletteSubtitle(paletteModule.id),
      body:
        paletteModule.id === "input" ||
        paletteModule.id === "agent" ||
        paletteModule.id === "output"
          ? ["画布新增节点", "已同步草稿契约"]
          : ["画布新增节点", "仅影响画布布局"],
      x: position.x,
      y: position.y,
      source: "canvas",
    };
    const nextExtraNodes = [...workflowExtraNodes, node];
    setWorkflowExtraNodes(nextExtraNodes);
    setWorkflowNodeTitles((current) => ({ ...current, [node.id]: node.title }));
    if (paletteModule.id === "input") {
      setBuilderInputSpec((current) =>
        appendCommaSpec(current, `${contractId}:free_text`),
      );
      setBuilderInputLabels((current) => ({
        ...current,
        [contractId]: paletteModule.label,
      }));
    } else if (paletteModule.id === "output") {
      setBuilderOutputSpec((current) =>
        appendCommaSpec(current, `${contractId}:json=${contractId}.json`),
      );
      setBuilderArtifacts((current) =>
        appendCommaSpec(current, `${contractId}.json`),
      );
      setBuilderOutputLabels((current) => ({
        ...current,
        [contractId]: paletteModule.label,
      }));
    } else if (paletteModule.id === "agent") {
      setBuilderGoal((current) =>
        current.includes(`新增智能体节点 ${contractId}`)
          ? current
          : `${current.trim()}\n\n新增智能体节点 ${contractId}: ${paletteModule.label}`.trim(),
      );
    }
    setActiveWorkflowNodeId(nodeId);
    setMessage(
      paletteModule.id === "input" ||
        paletteModule.id === "agent" ||
        paletteModule.id === "output"
        ? `画布节点已添加: ${paletteModule.label}；已同步草稿契约。`
        : `画布节点已添加: ${paletteModule.label}；当前只影响画布布局。`,
    );
    window.setTimeout(
      () => mergeWorkflowLayoutIntoJson(workflowLayoutSnapshot([...workflowCanvasNodes, node])),
      0,
    );
  }

  function addPaletteNodeToCanvasViewportCenter(moduleId: string) {
    const boardRect = workflowBoardRef.current?.getBoundingClientRect();
    if (!boardRect) return;
    addPaletteNodeToCanvas(
      moduleId,
      boardRect.left + Math.min(boardRect.width * 0.58, 560),
      boardRect.top + Math.min(boardRect.height * 0.45, 360),
    );
  }

  function startPalettePointerDrag(moduleId: string, event: ReactPointerEvent) {
    palettePointerDragRef.current = {
      moduleId,
      startX: event.clientX,
      startY: event.clientY,
    };
    const finishDrag = (pointerEvent: PointerEvent) => {
      const drag = palettePointerDragRef.current;
      palettePointerDragRef.current = null;
      window.removeEventListener("pointercancel", cancelDrag);
      if (!drag) return;
      const moved =
        Math.abs(pointerEvent.clientX - drag.startX) > 8 ||
        Math.abs(pointerEvent.clientY - drag.startY) > 8;
      const boardRect = workflowBoardRef.current?.getBoundingClientRect();
      const droppedOnBoard =
        boardRect &&
        pointerEvent.clientX >= boardRect.left &&
        pointerEvent.clientX <= boardRect.right &&
        pointerEvent.clientY >= boardRect.top &&
        pointerEvent.clientY <= boardRect.bottom;
      if (moved && droppedOnBoard) {
        addPaletteNodeToCanvas(
          drag.moduleId,
          pointerEvent.clientX,
          pointerEvent.clientY,
        );
      }
    };
    const cancelDrag = () => {
      palettePointerDragRef.current = null;
      window.removeEventListener("pointerup", finishDrag);
    };
    window.addEventListener("pointerup", finishDrag, { once: true });
    window.addEventListener("pointercancel", cancelDrag, { once: true });
  }

  function renameActiveWorkflowNode(title: string) {
    if (!activeWorkflowNode) return;
    setWorkflowNodeTitles((current) => ({
      ...current,
      [activeWorkflowNode.id]: title,
    }));
    setWorkflowExtraNodes((current) =>
      current.map((node) =>
        node.id === activeWorkflowNode.id ? { ...node, title } : node,
      ),
    );
    window.setTimeout(() => mergeWorkflowLayoutIntoJson(), 0);
  }

  function copyActiveWorkflowNode() {
    if (!activeWorkflowNode) return;
    const nodeId = `canvas-copy-${Date.now().toString(36)}`;
    const position = clampWorkflowNodePosition({
      x: activeWorkflowNode.x + 36,
      y: activeWorkflowNode.y + 36,
    });
    const copyNode: WorkflowCanvasNode = {
      ...activeWorkflowNode,
      id: nodeId,
      title: `${activeWorkflowNode.title} 副本`,
      subtitle: activeWorkflowNode.subtitle || "复制节点",
      body: ["复制的画布节点", "未自动改写字段契约"],
      x: position.x,
      y: position.y,
      source: "canvas",
    };
    const nextExtraNodes = [...workflowExtraNodes, copyNode];
    setWorkflowExtraNodes(nextExtraNodes);
    setWorkflowNodeTitles((current) => ({
      ...current,
      [nodeId]: copyNode.title,
    }));
    setActiveWorkflowNodeId(nodeId);
    setMessage(`节点已复制: ${copyNode.title}`);
    window.setTimeout(
      () => mergeWorkflowLayoutIntoJson(workflowLayoutSnapshot([...workflowCanvasNodes, copyNode])),
      0,
    );
  }

  function deleteActiveWorkflowNode() {
    if (!activeWorkflowNode) return;
    const nextNodes = workflowCanvasNodes.filter(
      (node) => node.id !== activeWorkflowNode.id,
    );
    let nextHidden = workflowHiddenNodeIds;
    if (activeWorkflowNode.source === "canvas") {
      setWorkflowExtraNodes((current) =>
        current.filter((node) => node.id !== activeWorkflowNode.id),
      );
    } else {
      nextHidden = Array.from(
        new Set([...workflowHiddenNodeIds, activeWorkflowNode.id]),
      );
      setWorkflowHiddenNodeIds(nextHidden);
    }
    setWorkflowNodePositions((current) => {
      const next = { ...current };
      delete next[activeWorkflowNode.id];
      return next;
    });
    setWorkflowNodeTitles((current) => {
      const next = { ...current };
      delete next[activeWorkflowNode.id];
      return next;
    });
    const nextEdges = workflowCanvasEdges.filter(
      (edge) =>
        edge.source !== activeWorkflowNode.id &&
        edge.target !== activeWorkflowNode.id,
    );
    setWorkflowCanvasEdges(nextEdges);
    setActiveWorkflowNodeId(nextNodes[0]?.id ?? "agent-task");
    setMessage(
      activeWorkflowNode.source === "canvas"
        ? `节点已删除: ${activeWorkflowNode.title}`
        : `契约节点已从画布隐藏: ${activeWorkflowNode.title}`,
    );
    mergeWorkflowLayoutIntoJson(
      workflowLayoutSnapshot(nextNodes, nextHidden, nextEdges),
    );
  }

  function resetActiveWorkflowNodePosition() {
    if (!activeWorkflowNode) return;
    setWorkflowNodePositions((current) => {
      const next = { ...current };
      delete next[activeWorkflowNode.id];
      return next;
    });
    setMessage(`节点位置已重置: ${activeWorkflowNode.title}`);
    window.setTimeout(() => mergeWorkflowLayoutIntoJson(), 0);
  }

  function applyWorkspaceSelection(workspace: Workspace) {
    workspaceAutoSelectionDoneRef.current = true;
    setWorkspaceId(workspace.id);
    setRepoPath(workspace.repo_path);
    setInputsJson((current) =>
      updateInputsJsonValue(
        current || "{}",
        { id: "repo_path", type: "directory" },
        workspace.repo_path,
      ),
    );
  }

  function workflowDefinitionForRun(workflowId: string): WorkflowDefinition | null {
    const registered = workflows.find((workflow) => workflow.id === workflowId);
    if (registered) return registered;
    const preset = workflowPresets.find(
      (item) => item.definition.id === workflowId || item.id === workflowId,
    );
    return preset?.definition ?? null;
  }

  function workflowInputDefaults(workflowId: string): Record<string, string> {
    const definition = workflowDefinitionForRun(workflowId);
    const nextInputs: Record<string, string> = {};
    for (const input of definition?.inputs ?? []) {
      if (!input || typeof input !== "object") continue;
      const inputId = String((input as Record<string, unknown>).id ?? "");
      if (!inputId) continue;
      nextInputs[inputId] = inputId === "repo_path" ? repoPath : "";
    }
    return nextInputs;
  }

  function selectRunWorkflow(workflowId: string) {
    setSelectedWorkflowId(workflowId);
    setInputsJson(pretty(workflowInputDefaults(workflowId)));
    setWorkflowInputsUpdated(true);
    window.setTimeout(() => setWorkflowInputsUpdated(false), 2200);
  }

  const loadWorkflows = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [workflowResult, presetResult] = await Promise.allSettled([
        api.workbench.workflows.list(),
        api.workbench.workflows.presets(),
      ]);

      const coreErrors: string[] = [];
      if (workflowResult.status === "fulfilled") {
        const nextWorkflowData = workflowResult.value;
        setWorkflows(nextWorkflowData);
        if (nextWorkflowData.length > 0) {
          const selectedWorkflow = nextWorkflowData.find(
            (item) => item.id === selectedWorkflowId,
          );
          const fallbackWorkflow = selectedWorkflow ?? nextWorkflowData[0];
          if (!selectedWorkflow) {
            setSelectedWorkflowId(fallbackWorkflow.id);
          }
          setWorkflowJson((currentJson) => {
            const currentId = workflowIdFromJson(currentJson);
            const currentIsEmpty = !currentJson.trim() || !currentId;
            const currentIsDefault = currentId === DEFAULT_WORKFLOW.id;
            if (currentIsDefault || currentIsEmpty) {
              return pretty(fallbackWorkflow);
            }
            return currentJson;
          });
          if (activeWorkbenchView === "workflow") {
            hydrateBuilderFromWorkflow(fallbackWorkflow);
          }
        }
      } else {
        coreErrors.push(
          workflowResult.reason instanceof Error
            ? workflowResult.reason.message
            : "Failed to load workflows",
        );
      }

      if (presetResult.status === "fulfilled") {
        const presetData = presetResult.value;
        setWorkflowPresets(presetData.items);
        if (!selectedPresetId && presetData.items.length > 0) {
          setSelectedPresetId(presetData.items[0].id);
        }
      } else {
        coreErrors.push(
          presetResult.reason instanceof Error
            ? presetResult.reason.message
            : "Failed to load workflow presets",
        );
      }

      const [
        taskRunResult,
        providerResult,
        workflowCapabilityResult,
        systemAuditResult,
        workspaceResult,
      ] = await Promise.allSettled([
        api.workbench.taskRuns.list({ limit: 10 }),
        api.workbench.providerCapabilities(),
        api.workbench.workflowCapabilities(),
        api.workbench.systemAudit(),
        api.workspaces.list(),
      ]);

      const diagnosticErrors: string[] = [];
      if (taskRunResult.status === "fulfilled") {
        setTaskRuns(taskRunResult.value.items);
      } else {
        diagnosticErrors.push("最近任务");
      }
      if (providerResult.status === "fulfilled") {
        setProviderMatrix(providerResult.value);
      } else {
        diagnosticErrors.push("执行器能力");
      }
      if (workflowCapabilityResult.status === "fulfilled") {
        setWorkflowCapabilities(workflowCapabilityResult.value);
        const defaults = (
          workflowCapabilityResult.value.skill_catalog ?? []
        )
          .filter((skill) => skill.default_enabled)
          .map((skill) => skill.id);
        if (defaults.length > 0) {
          setBuilderSkillIds((current) =>
            current.length > 0 ? current : defaults,
          );
        }
      } else {
        diagnosticErrors.push("工作流能力");
      }
      if (systemAuditResult.status === "fulfilled") {
        setSystemAudit(systemAuditResult.value);
      } else {
        diagnosticErrors.push("系统审计");
      }
      if (workspaceResult.status === "fulfilled") {
        const visibleWorkspaces = workspaceResult.value;
        setWorkspaces(visibleWorkspaces);
        if (
          !workspaceAutoSelectionDoneRef.current &&
          !repoPath.trim() &&
          visibleWorkspaces.length > 0
        ) {
          const preferred =
            visibleWorkspaces.find((workspace) => workspace.indexed === 1) ??
            visibleWorkspaces[0];
          applyWorkspaceSelection(preferred);
        }
      } else {
        diagnosticErrors.push("工作空间列表");
      }

      if (coreErrors.length > 0) {
        setError(coreErrors.join("; "));
      } else if (diagnosticErrors.length > 0) {
        setError(
          `工作流已加载，部分诊断数据加载失败: ${diagnosticErrors.join("、")}`,
        );
      }
    } catch (err: unknown) {
      setError(
        err instanceof Error ? err.message : "Failed to load workbench data",
      );
    } finally {
      setLoading(false);
    }
  }, [activeWorkbenchView, repoPath, selectedPresetId, selectedWorkflowId]);

  useEffect(() => {
    void loadWorkflows();
  }, [loadWorkflows]);

  async function runAction(name: string, action: () => Promise<void>) {
    if (busyActionRef.current) return;
    const startedAt = performance.now();
    busyActionRef.current = name;
    flushSync(() => {
      setBusyAction(name);
    });
    setError(null);
    setMessage(null);
    try {
      await action();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Action failed");
    } finally {
      const remainingBusyMs =
        MIN_VISIBLE_BUSY_ACTION_MS - (performance.now() - startedAt);
      if (remainingBusyMs > 0) {
        await new Promise((resolve) =>
          window.setTimeout(resolve, remainingBusyMs),
        );
      }
      if (busyActionRef.current === name) {
        busyActionRef.current = null;
        setBusyAction(null);
      }
    }
  }

  async function refreshArtifactManifest(taskRunId: string) {
    const manifest = await api.workbench.taskRuns.artifacts(taskRunId);
    setArtifactManifest(manifest);
  }

  async function restoreTaskRun(taskRunId: string) {
    const run = await api.workbench.taskRuns.get(taskRunId);
    const manifest = await api.workbench.taskRuns.artifacts(taskRunId);
    setPreparedRun(run);
    setArtifactManifest(manifest);
    setTaskRuns((current) =>
      [
        run,
        ...current.filter((item) => item.task_run_id !== run.task_run_id),
      ].slice(0, 10),
    );
    setExecutionResults({});
    setValidationResults({});
    setMaterializeResults({});
    setArtifactContent(null);
    setWorkflowOutputMaterialize(null);
    setSemanticOutputImport(null);
    setWorkflowExecution(null);
    setTaskRerunPlan(null);
    setTaskRerunPlanValidation(null);
    setTaskRerunExecution(null);
    setTaskRerunHistory(null);
    setTaskAcceptanceAudit(null);

    const artifactPaths = new Set(
      manifest.artifacts.map((item) => item.relative_path),
    );
    if (artifactPaths.has("workflow_execution.json")) {
      const content = await api.workbench.taskRuns.artifactContent(
        taskRunId,
        "workflow_execution.json",
      );
      const parsed = JSON.parse(
        content.content || "{}",
      ) as WorkflowExecutionResult;
      setWorkflowExecution(parsed);
      setTaskRerunPlan(
        (parsed.rerun_plan as TaskRerunPlan | undefined) ?? null,
      );
    }
    if (artifactPaths.has("workflow_output_materialization.json")) {
      const content = await api.workbench.taskRuns.artifactContent(
        taskRunId,
        "workflow_output_materialization.json",
      );
      const parsed = JSON.parse(
        content.content || "{}",
      ) as MaterializeWorkflowOutputsResult;
      setWorkflowOutputMaterialize(parsed);
    }
    if (artifactPaths.has("semantic_output_import.json")) {
      const content = await api.workbench.taskRuns.artifactContent(
        taskRunId,
        "semantic_output_import.json",
      );
      const parsed = JSON.parse(content.content || "{}") as {
        result?: SemanticCaseImportResult;
      };
      setSemanticOutputImport(parsed.result ?? null);
    }
    if (artifactPaths.has("task_rerun_plan.json")) {
      const [plan, validation, history] = await Promise.all([
        api.workbench.taskRuns.rerunPlan(taskRunId),
        api.workbench.taskRuns.rerunPlanValidation(taskRunId),
        api.workbench.taskRuns.rerunHistory(taskRunId),
      ]);
      setTaskRerunPlan(plan);
      setTaskRerunPlanValidation(validation);
      setTaskRerunHistory(history);
    }
    if (artifactPaths.has("task_acceptance_audit.json")) {
      const content = await api.workbench.taskRuns.artifactContent(
        taskRunId,
        "task_acceptance_audit.json",
      );
      const parsed = JSON.parse(
        content.content || "{}",
      ) as WorkbenchAcceptanceAudit;
      setTaskAcceptanceAudit(parsed);
    }
  }

  function hydrateBuilderFromWorkflow(workflow: {
    id?: unknown;
    name?: unknown;
    inputs?: unknown;
    outputs?: unknown;
    steps?: unknown;
  }) {
    const workflowId = String(workflow.id ?? "").trim();
    const workflowName = String(workflow.name ?? "").trim();
    if (workflowId) setBuilderWorkflowId(workflowId);
    if (workflowName) setBuilderWorkflowName(workflowName);

    const inputs = Array.isArray(workflow.inputs)
      ? workflow.inputs.filter(
          (item): item is Record<string, unknown> =>
            Boolean(item && typeof item === "object" && !Array.isArray(item)),
        )
      : [];
    const inputLabels: Record<string, string> = {};
    setBuilderInputSpec(
      inputs
        .map((input) => {
          const id = String(input.id ?? "").trim();
          if (!id) return "";
          const type = String(input.type ?? "free_text").trim() || "free_text";
          const resolver = String(input.resolver ?? "").trim();
          const label = String(input.label ?? "").trim();
          if (label && label !== id) inputLabels[id] = label;
          return workflowSpecToText({ id, type, resolver });
        })
        .filter(Boolean)
        .join(", "),
    );
    setBuilderInputLabels(inputLabels);

    const outputs = Array.isArray(workflow.outputs)
      ? workflow.outputs.filter(
          (item): item is Record<string, unknown> =>
            Boolean(item && typeof item === "object" && !Array.isArray(item)),
        )
      : [];
    const outputLabels: Record<string, string> = {};
    const outputArtifacts: string[] = [];
    setBuilderOutputSpec(
      outputs
        .map((output) => {
          const id = String(output.id ?? "").trim();
          if (!id) return "";
          const type = String(output.type ?? "json").trim() || "json";
          const artifact = String(output.artifact ?? "").trim();
          const label = String(output.label ?? "").trim();
          if (label && label !== id) outputLabels[id] = label;
          if (artifact) outputArtifacts.push(artifact);
          return workflowSpecToText({ id, type, artifact });
        })
        .filter(Boolean)
        .join(", "),
    );
    setBuilderOutputLabels(outputLabels);

    const steps = Array.isArray(workflow.steps)
      ? workflow.steps.filter(
          (item): item is Record<string, unknown> =>
            Boolean(item && typeof item === "object" && !Array.isArray(item)),
        )
      : [];
    const agentStep = steps.find(
      (step) => String(step.type ?? "") === "agent_task",
    );
    if (agentStep) {
      const provider = String(agentStep.provider ?? "").trim();
      const mcpProfile = String(agentStep.mcp_profile ?? "").trim();
      const goal = String(agentStep.goal ?? "").trim();
      const skills = Array.isArray(agentStep.skills)
        ? agentStep.skills.map((item) => String(item)).filter(Boolean)
        : [];
      const requiredArtifacts = Array.isArray(agentStep.required_artifacts)
        ? agentStep.required_artifacts.map((item) => String(item)).filter(Boolean)
        : [];
      if (provider) setBuilderProvider(provider);
      if (mcpProfile) setBuilderMcpProfile(mcpProfile);
      if (goal) setBuilderGoal(goal);
      if (skills.length > 0) setBuilderSkillIds(skills);
      setBuilderArtifacts(
        uniqueWorkflowStrings([...requiredArtifacts, ...outputArtifacts]).join(", "),
      );
    } else {
      setBuilderArtifacts(uniqueWorkflowStrings(outputArtifacts).join(", "));
    }
  }

  function applyBuilderScenario(
    scenarioId: keyof typeof WORKFLOW_BUILDER_SCENARIOS,
  ) {
    const scenario = WORKFLOW_BUILDER_SCENARIOS[scenarioId];
    setBuilderScenario(scenarioId);
    setBuilderWorkflowName(`自定义 ${scenario.name}`);
    setBuilderInputSpec(scenario.inputs);
    setBuilderOutputSpec(scenario.outputs);
    setBuilderGoal(scenario.goal);
    setBuilderArtifacts(scenario.artifacts);
    setBuilderSkillIds(
      "skills" in scenario
        ? [...scenario.skills]
        : DEFAULT_BUILDER_SKILL_IDS,
    );
    setBuilderInputSchemas(pretty(DEFAULT_BUILDER_INPUT_SCHEMAS));
    setBuilderOutputSchemas(pretty(DEFAULT_BUILDER_OUTPUT_SCHEMAS));
    setBuilderEvidenceMappings(pretty(DEFAULT_BUILDER_EVIDENCE_MAPPINGS));
    setBuilderSemanticImports(pretty(DEFAULT_BUILDER_SEMANTIC_IMPORTS));
    setBuilderInputLabels({});
    setBuilderOutputLabels({});
    setWorkflowNodePositions({});
    setWorkflowExtraNodes([]);
    setWorkflowHiddenNodeIds([]);
    setWorkflowNodeTitles({});
    setWorkflowCanvasEdges([]);
    setActiveWorkflowNodeId("agent-task");
  }

  function generateWorkflowFromBuilder(
    overrides: {
      inputSpec?: string;
      outputSpec?: string;
      artifacts?: string;
      inputLabels?: Record<string, string>;
      outputLabels?: Record<string, string>;
    } = {},
  ) {
    const workflowId = builderWorkflowId.trim();
    const workflowName = builderWorkflowName.trim();
    if (!workflowId || !workflowName) {
      throw new Error("Workflow builder requires workflow id and name");
    }
    const inputSchemas = parseJsonObject(builderInputSchemas || "{}");
    const inputSpec = overrides.inputSpec ?? builderInputSpec;
    const outputSpec = overrides.outputSpec ?? builderOutputSpec;
    const artifactsSpec = overrides.artifacts ?? builderArtifacts;
    const inputLabels = overrides.inputLabels ?? builderInputLabels;
    const outputLabels = overrides.outputLabels ?? builderOutputLabels;
    const inputs = parseWorkflowSpecList(inputSpec, "free_text").map(
      (input) => {
        const schema = inputSchemaForSpec(input.id, input.type, inputSchemas);
        const label = workflowItemLabel(inputLabels, input.id);
        return {
          id: input.id,
          label,
          type: input.type,
          required: input.type !== "file" && input.type !== "file_set",
          resolver:
            input.resolver ||
            (input.type === "mr_link" || input.type === "external_link"
              ? "agent_mcp"
              : "manual"),
          role:
            input.resolver === "agent_mcp" || input.type === "mr_link"
              ? "由智能体 CLI 通过 MCP 凭证解析远端变更源"
              : `用户提供: ${label}`,
          ...(schema ? { schema } : {}),
        };
      },
    );
    const requiredArtifacts = parseCommaSeparated(artifactsSpec);
    const selectedSkills = selectedBuilderSkillOptions.map((skill) => ({
      id: skill.id,
      label: skill.label,
      source: skill.source,
      prompt_hint: skill.prompt_hint || skill.description || skill.label,
    }));
    const outputSchemas = parseJsonObject(builderOutputSchemas || "{}");
    const evidenceMappings = parseJsonObject(builderEvidenceMappings || "{}");
    const semanticImports = parseJsonObject(builderSemanticImports || "{}");
    const outputs = parseWorkflowSpecList(outputSpec, "json").map(
      (output) => {
        const label = workflowItemLabel(outputLabels, output.id);
        const artifact =
          output.artifact ||
          outputArtifactForSpec(output.id, output.type, requiredArtifacts);
        const from = artifact ? "agent_collect" : "render_report";
        const schema =
          output.type === "json"
            ? outputSchemaForSpec(output.id, outputSchemas)
            : null;
        const evidenceMemory =
          output.type === "json" || output.type === "scope_report"
            ? outputEvidenceMappingForSpec(output.id, evidenceMappings)
            : null;
        const semanticImport =
          output.type === "test_cases"
            ? outputSemanticImportForSpec(
                output.id,
                output.type,
                semanticImports,
              )
            : null;
        return {
          id: output.id,
          label,
          type: output.type,
          from,
          ...(artifact ? { artifact } : {}),
          ...(schema ? { schema } : {}),
          ...(evidenceMemory ? { evidence_memory: evidenceMemory } : {}),
          ...(semanticImport ? { semantic_import: semanticImport } : {}),
        };
      },
    );
    const workflow = {
      id: workflowId,
      name: workflowName,
      version: 1,
      inputs,
      steps: [
        {
          id: "agent_collect",
          type: "agent_task",
          provider: builderProvider.trim() || "claude-code",
          mcp_profile: builderMcpProfile.trim(),
          skills: builderSkillIds,
          skill_instructions: selectedSkills,
          goal: builderGoal.trim(),
          required_artifacts: requiredArtifacts,
        },
        { id: "validate_evidence", type: "evidence_validate" },
        { id: "semantic_retrieve", type: "semantic_retrieve" },
        { id: "render_report", type: "report_render" },
      ],
      outputs,
      ui: {
        layout: workflowLayoutSnapshot(),
      },
    };
    setWorkflowJson(pretty(workflow));
    setSelectedWorkflowId(workflow.id);
    setMessage(`工作流草稿已生成: ${workflow.id}`);
  }

  const generateWorkflowDraft = () =>
    runAction("generate-workflow", async () => {
      generateWorkflowFromBuilder();
    });

  const generateAiWorkflowDraft = () =>
    runAction("generate-ai-workflow", async () => {
      const result = await api.workbench.workflows.generateDraft({
        prompt: aiWorkflowPrompt.trim(),
        preferred_id: aiWorkflowPreferredId.trim() || undefined,
        preferred_name: aiWorkflowPreferredName.trim() || undefined,
      });
      setAiWorkflowGeneration(result);
      setWorkflowJson(pretty(result.workflow));
      setSelectedWorkflowId(result.workflow.id);
      applyWorkflowLayout(result.workflow);
      setWorkflowDraftServerAudit(result.audit);
      setMessage(
        `AI 工作流草稿已生成: ${workflowDisplayName(result.workflow)} · ${result.generation_id}`,
      );
    });

  const saveWorkflow = () =>
    runAction("save-workflow", async () => {
      const payload = parseJsonObject(workflowJson);
      const saved = await api.workbench.workflows.create(payload);
      setSelectedWorkflowId(saved.id);
      hydrateBuilderFromWorkflow(saved as unknown as Record<string, unknown>);
      const warningCount = saved.audit?.warnings?.length ?? 0;
      setMessage(
        warningCount
          ? `工作流已保存: ${saved.id} (${warningCount} audit warning(s))`
          : `工作流已保存: ${saved.id}`,
      );
      await loadWorkflows();
    });

  const auditWorkflowDraft = () =>
    runAction("audit-workflow-draft", async () => {
      const payload = parseJsonObject(workflowJson);
      const audit = await api.workbench.workflows.auditDraft(payload);
      setWorkflowDraftServerAudit(audit);
      setMessage(
        audit.valid
          ? `工作流草稿审计: ${audit.status} (${audit.warnings.length} warning(s))`
          : `工作流草稿审计: invalid`,
      );
    });

  const duplicateSelectedWorkflowDraft = () => {
    const workflow = workflows.find((item) => item.id === selectedWorkflowId);
    if (!workflow) return;
    const clone = {
      ...workflow,
      id: `${workflow.id}_copy`,
      name: `${workflowDisplayName(workflow)} 副本`,
      version: Number(workflow.version ?? 1) + 1,
    };
    setWorkflowJson(pretty(clone));
    setSelectedWorkflowId(clone.id);
    applyWorkflowLayout(clone);
    setMessage(`已复制为草稿: ${clone.id}`);
  };

  const applyPreset = () => {
    const preset = workflowPresets.find((item) => item.id === selectedPresetId);
    if (!preset) return;
    setWorkflowJson(pretty(preset.definition));
    setSelectedWorkflowId(preset.definition.id);
    applyWorkflowLayout(preset.definition);
    setMessage(`已从模板库导入到当前草稿: ${workflowDisplayName(preset.definition)}`);
  };

  const restoreBuiltinPresets = () =>
    runAction("restore-builtin-presets", async () => {
      const result = await api.workbench.workflows.restoreBuiltins();
      setWorkflows(result.items);
      const preferred =
        result.items.find((item) => item.id === selectedWorkflowId) ??
        result.items.find((item) => item.id === "module_analysis") ??
        result.items[0];
      if (preferred) {
        setSelectedWorkflowId(preferred.id);
        setWorkflowJson(pretty(preferred));
        applyWorkflowLayout(preferred);
      }
      setMessage(`已恢复内置工作流: ${result.restored_count} 个预设`);
      await loadWorkflows();
    });

  const prepareTaskRun = () =>
    runAction("prepare-task-run", async () => {
      const inputs = parseJsonObject(inputsJson);
      const result = await api.workbench.taskRuns.prepare({
        workflow_id: selectedWorkflowId,
        workspace_id: workspaceId,
        repo_path: repoPath,
        inputs,
        provider_override: providerOverride.trim() || null,
      });
      setPreparedRun(result);
      setTaskRuns((current) =>
        [
          result,
          ...current.filter((item) => item.task_run_id !== result.task_run_id),
        ].slice(0, 10),
      );
      setExecutionResults({});
      setValidationResults({});
      setMaterializeResults({});
      setWorkflowExecution(null);
      setTaskRerunPlan(null);
      setTaskRerunPlanValidation(null);
      setTaskRerunExecution(null);
      setTaskRerunHistory(null);
      setTaskAcceptanceAudit(null);
      setWorkflowOutputMaterialize(null);
      setSemanticOutputImport(null);
      setArtifactContent(null);
      await refreshArtifactManifest(result.task_run_id);
      setMessage(`Task run prepared: ${result.task_run_id}`);
    });

  const createAndRunTaskRun = () =>
    runAction("create-and-run-task-run", async () => {
      const inputs = parseJsonObject(inputsJson);
      const result = await api.workbench.taskRuns.run(
        {
          workflow_id: selectedWorkflowId,
          workspace_id: workspaceId,
          repo_path: repoPath,
          inputs,
          provider_override: providerOverride.trim() || null,
        },
        90,
        true,
      );
      setPreparedRun(result.task_run);
      setTaskRuns((current) =>
        [
          result.task_run,
          ...current.filter((item) => item.task_run_id !== result.task_run_id),
        ].slice(0, 10),
      );
      setWorkflowExecution(result.execution);
      setWorkflowOutputMaterialize(result.evidence_materialization ?? null);
      setSemanticOutputImport(result.semantic_output_import ?? null);
      setTaskAcceptanceAudit(result.acceptance_audit ?? null);
      setTaskRerunPlan(
        (result.execution.rerun_plan as TaskRerunPlan | undefined) ?? null,
      );
      setTaskRerunPlanValidation(
        await api.workbench.taskRuns.rerunPlanValidation(result.task_run_id),
      );
      setExecutionResults({});
      setValidationResults({});
      setMaterializeResults({});
      setTaskRerunExecution(null);
      setTaskRerunHistory(null);
      setArtifactContent(null);
      await refreshArtifactManifest(result.task_run_id);
      await loadWorkflows();
      setMessage(
        `Task run ${result.status}: ${result.task_run_id}; evidence ${result.evidence_materialization?.status ?? "skipped"}; semantics ${result.semantic_output_import?.status ?? "skipped"}; audit ${result.acceptance_audit?.status ?? "skipped"}`,
      );
    });

  const restoreExistingTaskRun = (taskRunId: string) =>
    runAction(`restore-task-run-${taskRunId}`, async () => {
      await restoreTaskRun(taskRunId);
      setMessage(`Task run restored: ${taskRunId}`);
    });

  const runProviderStartupProbe = (provider: string) =>
    runAction(`provider-probe-${provider}`, async () => {
      const result = await api.tools.startupProbe(
        provider,
        repoPath.trim() || undefined,
      );
      setProviderProbeResults((current) => ({
        ...current,
        [provider]: result,
      }));
      setMessage(`启动探测 ${result.status}: ${provider}`);
    });

  const runProviderTaskProbe = (provider: string) =>
    runAction(`provider-task-probe-${provider}`, async () => {
      const result = await api.workbench.providerTaskProbe(
        provider,
        repoPath.trim() || undefined,
        30,
      );
      setProviderTaskProbeResults((current) => ({
        ...current,
        [provider]: result,
      }));
      setPreparedRun(result.task_run);
      setTaskRuns((current) =>
        [
          result.task_run,
          ...current.filter((item) => item.task_run_id !== result.task_run_id),
        ].slice(0, 10),
      );
      setWorkflowExecution(result.execution);
      setTaskAcceptanceAudit(result.acceptance_audit);
      setExecutionResults({});
      setValidationResults({});
      setMaterializeResults({});
      setTaskRerunPlan(null);
      setTaskRerunPlanValidation(null);
      setTaskRerunExecution(null);
      setTaskRerunHistory(null);
      setWorkflowOutputMaterialize(null);
      setSemanticOutputImport(null);
      setArtifactContent(null);
      await refreshArtifactManifest(result.task_run_id);
      setMessage(
        `任务探测 ${result.status}: ${provider} contract ${result.summary.task_contract_status}`,
      );
    });

  const runAllAgentProviderStartupProbes = () =>
    runAction("provider-probe-all-agents", async () => {
      const providers = (providerMatrix?.providers ?? []).filter(
        (provider) =>
          provider.agent_owned && provider.diagnostics?.startup_probe_endpoint,
      );
      const result = await api.workbench.deploymentProbe(
        repoPath.trim() || undefined,
        providers.map((provider) => provider.provider),
      );
      setDeploymentProbeResult(result);
      setProviderProbeResults((current) => {
        const next = { ...current };
        for (const item of result.providers) {
          const provider = item.provider || item.tool || "";
          if (provider) {
            next[provider] = item;
          }
        }
        return next;
      });
      setMessage(
        `部署探测 ${result.status}: ${result.summary.healthy_count}/${result.summary.provider_count} healthy`,
      );
    });

  const runAllAgentProviderTaskProbes = () =>
    runAction("provider-task-probe-all-agents", async () => {
      const providers = (providerMatrix?.providers ?? []).filter(
        (provider) => provider.agent_owned && provider.command.length > 0,
      );
      const result = await api.workbench.deploymentProbe(
        repoPath.trim() || undefined,
        providers.map((provider) => provider.provider),
        true,
        30,
      );
      setDeploymentProbeResult(result);
      setProviderProbeResults((current) => {
        const next = { ...current };
        for (const item of result.providers) {
          const provider = item.provider || item.tool || "";
          if (provider) {
            next[provider] = item;
          }
        }
        return next;
      });
      setProviderTaskProbeResults((current) => {
        const next = { ...current };
        for (const item of result.providers) {
          const provider = item.provider || item.tool || "";
          if (provider && item.task_probe) {
            next[provider] = item.task_probe;
          }
        }
        return next;
      });
      const ready = result.summary.task_ready_count ?? 0;
      const total = result.summary.provider_count;
      setMessage(
        `任务探测 deployment ${result.status}: ${ready}/${total} ready`,
      );
    });

  const runSmokeE2E = () =>
    runAction("smoke-e2e", async () => {
      const result = await api.workbench.smokeE2E(
        repoPath.trim() || undefined,
        30,
      );
      setSmokeE2EResult(result);
      setPreparedRun(result.task_run);
      setTaskRuns((current) =>
        [
          result.task_run,
          ...current.filter((item) => item.task_run_id !== result.task_run_id),
        ].slice(0, 10),
      );
      setWorkflowExecution(result.execution);
      setTaskAcceptanceAudit(result.acceptance_audit);
      setExecutionResults({});
      setValidationResults({});
      setMaterializeResults({});
      setTaskRerunPlan(null);
      setTaskRerunPlanValidation(null);
      setTaskRerunExecution(null);
      setTaskRerunHistory(null);
      setWorkflowOutputMaterialize(null);
      setSemanticOutputImport(null);
      setArtifactContent(null);
      await refreshArtifactManifest(result.task_run_id);
      setMessage(`全链路烟测 ${result.status}: ${result.task_run_id}`);
    });

  function updatePrepareInput(input: Record<string, unknown>, value: string) {
    setInputsJson((current) => updateInputsJsonValue(current, input, value));
  }

  const uploadPrepareInputFile = (
    input: Record<string, unknown>,
    files: FileList | null,
  ) =>
    runAction(`upload-input-${String(input.id ?? "input")}`, async () => {
      if (!files || files.length === 0) return;
      const inputId = String(input.id ?? "");
      const inputType = String(input.type ?? "");
      const uploads = await Promise.all(
        Array.from(files).map((file) =>
          api.workbench.uploadInputFile(file, inputId),
        ),
      );
      const paths = uploads.map((item) => item.path).filter(Boolean);
      if (inputType === "file_set") {
        setInputsJson((current) => {
          const existing = inputTextValue(
            parseJsonObject(current || "{}"),
            input,
          )
            .split(/\r?\n/)
            .map((line) => line.trim())
            .filter(Boolean);
          return updateInputsJsonValue(
            current,
            input,
            [...existing, ...paths].join("\n"),
          );
        });
      } else if (paths[0]) {
        updatePrepareInput(input, paths[0]);
      }
      setMessage(
        `Input file uploaded: ${uploads.map((item) => item.filename).join(", ")}`,
      );
    });

  const openPreparedConversation = async () => {
    if (!preparedRun || taskRunActionBusy) return;
    setOpeningConversation(true);
    try {
      const conversation = await api.aiConversations.createForScope({
        scope_type: "workbench_task_run",
        scope_id: preparedRun.task_run_id,
        workspace_id: preparedRun.workspace_id,
        memory_namespace: `workspace:${preparedRun.workspace_id}`,
        title: `${workflowDisplayName(preparedRun.workflow_id)} · AI 复盘`,
        initial_context: {
          workflow_id: preparedRun.workflow_id,
          task_run_id: preparedRun.task_run_id,
          workspace_id: preparedRun.workspace_id,
          memory_namespace: `workspace:${preparedRun.workspace_id}`,
          repo_path: preparedRun.repo_path,
          artifact_dir: preparedRun.artifact_dir,
          agent_runs_count: preparedRun.agent_runs.length,
          agent_runs: preparedRun.agent_runs.map((agentRun) => ({
            step_id: agentRun.step_id,
            run_id: agentRun.run_id,
            artifact_dir: agentRun.artifact_dir,
          })),
        },
      });
      router.push(`/ai/${conversation.id}`);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "创建 AI 线程失败");
    } finally {
      setOpeningConversation(false);
    }
  };

  const loadPreparedArtifacts = () =>
    runAction("load-artifacts", async () => {
      if (!preparedRun) return;
      await refreshArtifactManifest(preparedRun.task_run_id);
      setMessage(`产物已加载: ${preparedRun.task_run_id}`);
    });

  const loadTaskRerunPlan = () =>
    runAction("load-rerun-plan", async () => {
      if (!preparedRun) return;
      const [result, validation] = await Promise.all([
        api.workbench.taskRuns.rerunPlan(preparedRun.task_run_id),
        api.workbench.taskRuns.rerunPlanValidation(preparedRun.task_run_id),
      ]);
      const history = await api.workbench.taskRuns.rerunHistory(
        preparedRun.task_run_id,
      );
      setTaskRerunPlan(result);
      setTaskRerunPlanValidation(validation);
      setTaskRerunHistory(history);
      setMessage(`Rerun plan ${result.status}: ${result.task_run_id}`);
    });

  const generateTaskAcceptanceAudit = () =>
    runAction("acceptance-audit", async () => {
      if (!preparedRun) return;
      const result = await api.workbench.taskRuns.acceptanceAudit(
        preparedRun.task_run_id,
      );
      setTaskAcceptanceAudit(result);
      await refreshArtifactManifest(preparedRun.task_run_id);
      setMessage(
        `Acceptance audit ${result.status}: ${result.summary.missing_required} missing required`,
      );
    });

  const executeTaskRerunPlan = () =>
    runAction("execute-rerun-plan", async () => {
      if (!preparedRun || !taskRerunPlanValidation?.can_rerun) return;
      const result = await api.workbench.taskRuns.executeRerunPlan(
        preparedRun.task_run_id,
        90,
        true,
      );
      setTaskRerunExecution(result);
      if (result.execution) {
        setWorkflowExecution({
          ...result.execution,
          evidence_materialization:
            result.evidence_materialization ??
            result.execution.evidence_materialization,
          semantic_output_import:
            result.semantic_output_import ??
            result.execution.semantic_output_import,
          acceptance_audit:
            result.acceptance_audit ?? result.execution.acceptance_audit,
        });
        setTaskRerunPlan(
          (result.execution.rerun_plan as TaskRerunPlan | undefined) ?? null,
        );
      }
      setWorkflowOutputMaterialize(result.evidence_materialization ?? null);
      setSemanticOutputImport(result.semantic_output_import ?? null);
      setTaskRerunPlanValidation(result.validation_after ?? null);
      setTaskRerunHistory(
        await api.workbench.taskRuns.rerunHistory(preparedRun.task_run_id),
      );
      setTaskAcceptanceAudit(result.acceptance_audit ?? null);
      await refreshArtifactManifest(preparedRun.task_run_id);
      setMessage(
        `Rerun execution ${result.execution?.status ?? result.status}: ${preparedRun.task_run_id}; evidence ${result.evidence_materialization?.status ?? "skipped"}; semantics ${result.semantic_output_import?.status ?? "skipped"}; audit ${result.acceptance_audit?.status ?? "skipped"}`,
      );
    });

  const previewArtifact = (relativePath: string) =>
    runAction(`preview-artifact-${relativePath}`, async () => {
      if (!preparedRun || taskRunActionBusy) return;
      const result = await api.workbench.taskRuns.artifactContent(
        preparedRun.task_run_id,
        relativePath,
      );
      setArtifactContent(result);
      setMessage(`Artifact preview loaded: ${relativePath}`);
    });

  const executePreparedWorkflow = () =>
    runAction("execute-workflow", async () => {
      if (!preparedRun) return;
      const result = await api.workbench.taskRuns.execute(
        preparedRun.task_run_id,
        90,
        true,
      );
      setWorkflowExecution(result);
      setWorkflowOutputMaterialize(result.evidence_materialization ?? null);
      setSemanticOutputImport(result.semantic_output_import ?? null);
      setTaskRerunPlan(
        (result.rerun_plan as TaskRerunPlan | undefined) ?? null,
      );
      setTaskRerunPlanValidation(
        await api.workbench.taskRuns.rerunPlanValidation(
          preparedRun.task_run_id,
        ),
      );
      setTaskAcceptanceAudit(result.acceptance_audit ?? null);
      await refreshArtifactManifest(preparedRun.task_run_id);
      setMessage(
        `Workflow execution ${result.status}: ${result.task_run_id}; evidence ${result.evidence_materialization?.status ?? "skipped"}; semantics ${result.semantic_output_import?.status ?? "skipped"}; audit ${result.acceptance_audit?.status ?? "skipped"}`,
      );
      await loadWorkflows();
    });

  const materializePreparedWorkflowOutputs = () =>
    runAction("materialize-workflow-outputs", async () => {
      if (!preparedRun) return;
      const result = await api.workbench.taskRuns.materializeOutputs(
        preparedRun.task_run_id,
      );
      setWorkflowOutputMaterialize(result);
      setSemanticOutputImport(result.semantic_output_import ?? null);
      await refreshArtifactManifest(preparedRun.task_run_id);
      setMessage(
        `Workflow outputs materialized: ${result.evidence_count}; semantics ${result.semantic_output_import?.status ?? "skipped"}`,
      );
    });

  const importPreparedSemanticOutputs = () =>
    runAction("import-semantic-outputs", async () => {
      if (!preparedRun) return;
      const result = await api.workbench.taskRuns.importSemanticOutputs(
        preparedRun.task_run_id,
        { output_ids: semanticImportOutputIds },
      );
      setSemanticOutputImport(result);
      await refreshArtifactManifest(preparedRun.task_run_id);
      setMessage(
        `Semantic outputs imported: ${result.imported_count}, rejected: ${result.rejected_count}`,
      );
    });

  const executePreparedAgentRun = (stepId: string) =>
    runAction(`execute-${stepId}`, async () => {
      if (!preparedRun) return;
      const result = await api.workbench.taskRuns.executeAgentRun(
        preparedRun.task_run_id,
        stepId,
        90,
      );
      setExecutionResults((current) => ({ ...current, [stepId]: result }));
      setMessage(`Agent run ${result.status}: ${result.run_id}`);
    });

  const validatePreparedAgentRun = (
    stepId: string,
    requiredArtifacts: string[],
  ) =>
    runAction(`validate-${stepId}`, async () => {
      if (!preparedRun) return;
      const result = await api.workbench.taskRuns.validateMrArtifacts(
        preparedRun.task_run_id,
        stepId,
        requiredArtifacts,
      );
      setValidationResults((current) => ({ ...current, [stepId]: result }));
      setMessage(`Artifact validation ${result.status}: ${stepId}`);
    });

  const materializePreparedAgentRun = (
    stepId: string,
    requiredArtifacts: string[],
  ) =>
    runAction(`materialize-${stepId}`, async () => {
      if (!preparedRun) return;
      const result = await api.workbench.taskRuns.materializeEvidence(
        preparedRun.task_run_id,
        stepId,
        requiredArtifacts,
        `${preparedRun.workflow_id} ${preparedRun.task_run_id}`,
      );
      setMaterializeResults((current) => ({ ...current, [stepId]: result }));
      setMessage(`证据已固化: ${result.evidence_count}`);
    });

  const importSemanticCase = () =>
    runAction("import-semantic-case", async () => {
      const payload = parseJsonValue(semanticJson);
      if (isBulkSemanticImportPayload(payload)) {
        const result = await api.workbench.semanticCases.importMany(payload);
        setMessage(
          `语义用例已导入: ${result.imported_count}, rejected: ${result.rejected_count}`,
        );
        return;
      }
      if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
        throw new Error("Semantic import JSON must be an object or array");
      }
      const result = await api.workbench.semanticCases.create(
        payload as Record<string, unknown>,
      );
      setMessage(`语义用例已保存: ${result.case_id}`);
    });

  const buildSemanticCasesFromText = () =>
    runAction("build-semantic-cases", async () => {
      const payload = semanticCasesFromLines({
        feature: semanticFeature,
        module: semanticModule,
        text: semanticLines,
      });
      setSemanticJson(pretty(payload));
      const count = Array.isArray(payload.cases) ? payload.cases.length : 0;
      setMessage(`语义导入草稿已生成: ${count} cases`);
    });

  const searchSemanticCases = () =>
    runAction("search-semantic-cases", async () => {
      const result = await api.workbench.semanticCases.search({
        q: semanticQuery,
        limit: 10,
      });
      setSemanticResults(result.items);
      setMessage(`语义搜索结果: ${result.items.length}`);
    });

  const importSemanticCaseFile = () =>
    runAction("import-semantic-file", async () => {
      if (!semanticFile) {
        throw new Error("Select a semantic case file first");
      }
      const result = await api.workbench.semanticCases.importFile(
        semanticFile,
        {
          feature: semanticFeature,
          module: semanticModule,
          test_level: "black_box",
        },
      );
      setMessage(
        `语义文件已导入: ${result.imported_count}, rejected: ${result.rejected_count}`,
      );
      setSemanticFile(null);
    });

  const saveManualEvidence = () =>
    runAction("save-manual-evidence", async () => {
      const subject = manualEvidenceSubject.trim();
      if (!subject) {
        throw new Error("Evidence subject is required");
      }
      const run = await api.workbench.memory.createRun({
        workspace_id: workspaceId,
        repo_path: repoPath,
        object_text: subject,
        workflow_id: "manual_evidence_entry",
        status: "completed",
      });
      const result = await api.workbench.memory.createEvidence({
        run_id: run.run_id,
        workspace_id: workspaceId,
        kind: "manual_source_evidence",
        subject_key: subject,
        status: "accepted",
        source: "workbench_manual_entry",
        path: manualEvidencePath.trim(),
        reason: manualEvidenceText.trim(),
        text: manualEvidenceText.trim(),
        confidence: 1,
        provenance: {
          repo_path: repoPath,
          line_start: 1,
          entry_method: "workbench_manual_evidence_form",
        },
      });
      setMemoryQuery(subject);
      setMessage(
        `证据已保存: ${result.evidence_id}; source slices ${result.source_slice_count ?? 0}`,
      );
    });

  const searchMemory = () =>
    runAction("search-memory", async () => {
      const result = await api.workbench.memory.search({
        q: memoryQuery,
        limit: 10,
      });
      setMemoryResults(result.items);
      setMemorySlices({});
      setMessage(`证据搜索结果: ${result.items.length}`);
    });

  const loadMemorySlices = (evidenceId: string) =>
    runAction(`memory-slices-${evidenceId}`, async () => {
      const result = await api.workbench.memory.sourceSlices(evidenceId);
      setMemorySlices((current) => ({
        ...current,
        [evidenceId]: result.items,
      }));
      setMessage(`源码切片已加载: ${result.items.length}`);
    });

  const taskRunActionBusy = Boolean(busyAction);
  const agentRunActionBusy = Boolean(
    busyAction?.startsWith("execute-") ||
    busyAction?.startsWith("validate-") ||
      busyAction?.startsWith("materialize-"),
  );
  const pageTitle =
    activeWorkbenchView === "workflow"
      ? "工作流设计"
      : activeWorkbenchView === "knowledge"
        ? "语义库"
        : "运行驾驶舱";
  const pageDescription =
    activeWorkbenchView === "workflow"
      ? "创建、编辑并保存工作流模板；保存后的模板才会出现在运行驾驶舱。"
      : activeWorkbenchView === "knowledge"
        ? "搜索、导入和复用测试知识、历史案例与证据片段。"
        : "选择已建工作区和已保存工作流，填写本次输入，启动运行并下载交付文件。";

  return (
    <div
      ref={workbenchRootRef}
      className="ct-workbench-shell w-full px-4 xl:px-6"
    >
      <div className="ct-workbench-hero ct-liquid-glass mb-3 overflow-hidden rounded-xl px-3 py-2.5">
        <div className="flex flex-col gap-2 xl:flex-row xl:items-center xl:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <p className="font-data text-[11px] uppercase tracking-[0.14em] text-primary">
                Agent Workflow
              </p>
              <h1 className="font-display text-sm font-semibold text-on-surface sm:text-base">
                {pageTitle}
              </h1>
            </div>
            <p className="mt-1 max-w-4xl text-xs leading-4 text-on-surface-variant">
              {pageDescription}
            </p>
          </div>
          <button
            onClick={() => void loadWorkflows()}
            disabled={loading}
            className="ct-liquid-button inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md bg-primary px-2.5 py-1.5 text-xs font-medium text-on-primary disabled:opacity-50"
          >
            {loading ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <RefreshCw size={14} />
            )}
            刷新状态
          </button>
        </div>

        <div className="mt-2 flex flex-wrap gap-1.5">
          {[
            ["系统", systemAudit?.status ?? "待检查"],
            ["预设", `${workflowPresets.length}/${workflows.length}`],
            ["工作区", String(workspaces.length)],
            ["任务", String(taskRuns.length)],
          ].map(([label, value]) => (
            <span
              key={label}
              className="inline-flex items-center gap-1.5 rounded-md border border-outline-variant/25 bg-surface-container/75 px-2 py-0.5 text-[11px] text-on-surface-variant"
            >
              <span>{label}</span>
              <span className="font-data font-semibold text-on-surface">
                {value}
              </span>
            </span>
          ))}
        </div>
      </div>

      {(error || message) && (
        <div
          className={`mb-5 rounded-lg border px-4 py-3 text-sm ${
            error
              ? "border-red-500/20 bg-red-500/10 text-red-400"
              : "border-green-500/20 bg-green-500/10 text-green-400"
          }`}
        >
          {error ?? message}
        </div>
      )}

      <WorkbenchStageFrame
        activeWorkbenchView={activeWorkbenchView}
        reducedMotion={motionPreferenceReady && Boolean(prefersReducedMotion)}
      >
        {activeWorkbenchView === "diagnostics" && (
          <Panel title="执行器矩阵" icon={<AlertTriangle size={16} />}>
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <p className="text-xs text-on-surface-variant">
                这里检查本机后端能否调用外部智能体 CLI，以及这些执行器是否具备
                MCP 凭证、产物导出和任务探测能力。
              </p>
              <button
                onClick={() => runAllAgentProviderStartupProbes()}
                disabled={
                  busyAction === "provider-probe-all-agents" ||
                  !(providerMatrix?.providers ?? []).some(
                    (provider) =>
                      provider.agent_owned &&
                      provider.diagnostics?.startup_probe_endpoint,
                  )
                }
                className="inline-flex items-center gap-2 rounded-lg bg-surface-container px-2.5 py-1.5 text-xs font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
              >
                {busyAction === "provider-probe-all-agents" ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : (
                  <PlayCircle size={13} />
                )}
                探测全部 Agent
              </button>
              <button
                onClick={() => runAllAgentProviderTaskProbes()}
                disabled={
                  busyAction === "provider-task-probe-all-agents" ||
                  !(providerMatrix?.providers ?? []).some(
                    (provider) =>
                      provider.agent_owned && provider.command.length > 0,
                  )
                }
                className="inline-flex items-center gap-2 rounded-lg bg-primary px-2.5 py-1.5 text-xs font-medium text-on-primary transition-opacity hover:opacity-90 disabled:opacity-50"
              >
                {busyAction === "provider-task-probe-all-agents" ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : (
                  <PlayCircle size={13} />
                )}
                任务探测
              </button>
              <button
                onClick={runSmokeE2E}
                disabled={busyAction === "smoke-e2e"}
                className="inline-flex items-center gap-2 rounded-lg bg-primary px-2.5 py-1.5 text-xs font-medium text-on-primary transition-opacity hover:opacity-90 disabled:opacity-50"
              >
                {busyAction === "smoke-e2e" ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : (
                  <PlayCircle size={13} />
                )}
                全链路烟测
              </button>
            </div>
            {smokeE2EResult && (
              <div className="mb-3 rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 text-xs text-on-surface-variant">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium text-on-surface">
                    全链路烟测
                  </span>
                  <span
                    className={
                      smokeE2EResult.status === "ready"
                        ? "font-data text-green-500"
                        : "font-data text-warning"
                    }
                  >
                    {smokeE2EResult.status}
                  </span>
                  <span className="font-data">
                    task:{smokeE2EResult.task_run_id}
                  </span>
                  <span className="font-data">
                    execution:{smokeE2EResult.execution.status}
                  </span>
                  <span className="font-data">
                    missing:
                    {smokeE2EResult.acceptance_audit.summary.missing_required}
                  </span>
                </div>
                <p className="mt-1 break-words font-data text-[10px]">
                  artifact:{smokeE2EResult.artifact.path}
                </p>
              </div>
            )}
            {deploymentProbeResult && (
              <div className="mb-3 rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 text-xs text-on-surface-variant">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium text-on-surface">部署探测</span>
                  <span
                    className={
                      deploymentProbeResult.status === "healthy"
                        ? "font-data text-green-500"
                        : "font-data text-warning"
                    }
                  >
                    {deploymentProbeResult.status}
                  </span>
                  <span className="font-data">
                    healthy:{deploymentProbeResult.summary.healthy_count}/
                    {deploymentProbeResult.summary.provider_count}
                  </span>
                  <span className="font-data">
                    failed:{deploymentProbeResult.summary.failed_count}
                  </span>
                  {deploymentProbeResult.summary.task_contract_probe && (
                    <span className="font-data">
                      task-ready:
                      {deploymentProbeResult.summary.task_ready_count ?? 0}/
                      {deploymentProbeResult.summary.provider_count}
                    </span>
                  )}
                  {typeof deploymentProbeResult.evidence_count === "number" && (
                    <span className="font-data">
                      evidence:{deploymentProbeResult.evidence_count}
                    </span>
                  )}
                  <span className="font-data">
                    probe:{deploymentProbeResult.probe_id}
                  </span>
                </div>
                <p className="mt-1 break-words font-data text-[10px]">
                  artifact:
                  {deploymentProbeResult.artifact.latest_path ||
                    deploymentProbeResult.artifact.path}
                </p>
                {deploymentProbeResult.evidence_ids?.length ? (
                  <p className="mt-1 break-words font-data text-[10px]">
                    evidence_ids:{deploymentProbeResult.evidence_ids.join(", ")}
                  </p>
                ) : null}
              </div>
            )}
            <div className="grid gap-4 [grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr))]">
              {(providerMatrix?.providers ?? []).map((provider) => (
                <div
                  key={provider.provider}
                  className="ct-provider-card min-w-0 rounded-xl border border-outline-variant/30 bg-surface/80 p-4 text-xs"
                >
                  <div className="ct-provider-card-header flex items-start justify-between gap-3">
                    <div className="min-w-0 space-y-1">
                      <p className="ct-provider-name truncate text-sm font-semibold text-on-surface">
                        {provider.display_name || provider.provider}
                      </p>
                      <p className="ct-provider-slug font-data text-[11px] text-on-surface-variant">
                        {provider.provider}
                      </p>
                    </div>
                    <span className="ct-provider-status-badge shrink-0 rounded bg-surface-container px-2 py-0.5 font-data text-[10px] text-on-surface-variant">
                      {provider.status}
                    </span>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {provider.codetalk_callable && (
                      <span className="ct-provider-pill ct-provider-pill--green rounded bg-green-400/10 px-2 py-0.5 text-[11px] font-medium text-green-500">
                        CodeTalk 可直接调用
                      </span>
                    )}
                    {provider.agent_owned && (
                      <span className="ct-provider-pill ct-provider-pill--dark rounded bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary">
                        Agent 持有凭证
                      </span>
                    )}
                    {!provider.codetalk_callable && !provider.agent_owned && (
                      <span className="ct-provider-pill ct-provider-pill--amber rounded bg-amber-400/10 px-2 py-0.5 text-[11px] font-medium text-amber-500">
                        委托或不可用
                      </span>
                    )}
                  </div>
                  <div className="ct-provider-facts mt-3">
                    <ProviderFactRow
                      label="归属"
                      value={
                        <span className="font-data">{provider.owner}</span>
                      }
                    />
                    <ProviderFactRow
                      label="命令"
                      value={
                        <span className="font-data">
                          {provider.command.length > 0
                            ? provider.command.join(" ")
                            : "n/a"}
                        </span>
                      }
                    />
                    <ProviderFactRow
                      label="MCP"
                      value={
                        <span className="font-data">
                          {provider.capabilities.supports_mcp
                            ? provider.capabilities.mcp_profiles.length > 0
                              ? provider.capabilities.mcp_profiles.join(", ")
                              : "yes"
                            : "no"}
                        </span>
                      }
                    />
                    <ProviderFactRow
                      label="产物"
                      value={
                        <span className="font-data">
                          {provider.capabilities.supports_artifact_export
                            ? "artifact"
                            : "no-artifact"}
                        </span>
                      }
                    />
                    <ProviderFactRow
                      label="JSON"
                      value={
                        <span className="font-data">
                          {provider.capabilities.supports_json_output
                            ? "json"
                            : "no-json"}
                        </span>
                      }
                    />
                    {provider.env_hint_keys?.length ? (
                      <ProviderFactRow
                        label="环境变量"
                        value={
                          <span className="font-data">
                            {provider.env_hint_keys.join(", ")}
                          </span>
                        }
                      />
                    ) : null}
                  </div>
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {provider.capabilities.supports_source_discovery && (
                      <span className="ct-provider-feature rounded bg-surface-container px-2 py-0.5 text-[11px] text-on-surface">
                        源码发现
                      </span>
                    )}
                    {provider.capabilities.supports_call_graph && (
                      <span className="ct-provider-feature rounded bg-surface-container px-2 py-0.5 text-[11px] text-on-surface">
                        调用图
                      </span>
                    )}
                    {provider.capabilities.supports_source_slices && (
                      <span className="ct-provider-feature rounded bg-surface-container px-2 py-0.5 text-[11px] text-on-surface">
                        源码切片
                      </span>
                    )}
                    {provider.capabilities.supports_black_box_terms && (
                      <span className="ct-provider-feature rounded bg-surface-container px-2 py-0.5 text-[11px] text-on-surface">
                        黑盒术语
                      </span>
                    )}
                  </div>
                  {provider.credential_boundary && (
                    <p className="ct-provider-note mt-3 text-xs leading-5 text-on-surface-variant">
                      {provider.credential_boundary}
                    </p>
                  )}
                  {provider.diagnostics && (
                    <div className="ct-provider-diagnostics mt-3 space-y-2 border-t border-outline-variant/30 pt-3 text-on-surface-variant">
                      <ProviderSectionTitle>启动探测</ProviderSectionTitle>
                      {provider.diagnostics.startup_probe_endpoint && (
                        <ProviderFactRow
                          label="Probe"
                          value={
                            <span className="font-data">
                              {provider.diagnostics.startup_probe_endpoint}
                            </span>
                          }
                        />
                      )}
                      {provider.diagnostics.startup_probe_transport && (
                        <ProviderFactRow
                          label="传输"
                          value={
                            <span className="font-data">
                              {provider.diagnostics.startup_probe_transport}
                            </span>
                          }
                        />
                      )}
                      {provider.diagnostics.command_resolution && (
                        <div className="ct-provider-diag-box rounded bg-surface-container px-2 py-1.5">
                          <p className="ct-provider-diag-head">
                            <span>解析</span>
                            <span className="font-data">
                              {provider.diagnostics.command_resolution.status ||
                                "unknown"}
                            </span>
                            {provider.diagnostics.command_resolution
                              .used_fallback && (
                              <span className="ct-provider-mini-badge font-medium text-warning">
                                fallback
                              </span>
                            )}
                            {provider.diagnostics.command_resolution
                              .launch_kind && (
                              <span className="ct-provider-mini-badge font-data text-on-surface">
                                launch:
                                {
                                  provider.diagnostics.command_resolution
                                    .launch_kind
                                }
                              </span>
                            )}
                          </p>
                          {provider.diagnostics.command_resolution.reason && (
                            <p className="mt-1 break-words">
                              原因:{" "}
                              {provider.diagnostics.command_resolution.reason}
                            </p>
                          )}
                          {typeof provider.diagnostics.command_resolution
                            .attempt_count === "number" && (
                            <p className="mt-1">
                              尝试次数:{" "}
                              <span className="font-data text-on-surface">
                                {
                                  provider.diagnostics.command_resolution
                                    .attempt_count
                                }
                              </span>
                            </p>
                          )}
                          {(() => {
                            const attempts =
                              provider.diagnostics.command_resolution
                                ?.attempts ?? [];
                            const lastAttempt = attempts[attempts.length - 1];
                            const resolutionLines = commandResolutionLines(
                              lastAttempt?.resolution,
                            );
                            if (resolutionLines.length === 0) return null;
                            return (
                              <div className="mt-2 space-y-1">
                                {resolutionLines.map((line) => (
                                  <p
                                    key={line}
                                    className="break-words font-data text-[11px] text-on-surface"
                                  >
                                    {line}
                                  </p>
                                ))}
                              </div>
                            );
                          })()}
                        </div>
                      )}
                      {provider.diagnostics.probe_recipe && (
                        <div className="rounded bg-surface-container px-2 py-1.5">
                          <p className="font-medium text-on-surface">
                            探测配方
                          </p>
                          {provider.diagnostics.probe_recipe
                            .startup_probe_http && (
                            <p className="mt-1 break-words">
                              HTTP:{" "}
                              <span className="font-data text-on-surface">
                                {
                                  provider.diagnostics.probe_recipe
                                    .startup_probe_http
                                }
                              </span>
                            </p>
                          )}
                          {provider.diagnostics.probe_recipe
                            .backend_command && (
                            <p className="mt-1 break-words">
                              后端命令:{" "}
                              <span className="font-data text-on-surface">
                                {
                                  provider.diagnostics.probe_recipe
                                    .backend_command
                                }
                              </span>
                            </p>
                          )}
                          {provider.diagnostics.probe_recipe.command_env && (
                            <p className="mt-1 break-words">
                              覆盖环境变量:{" "}
                              <span className="font-data text-on-surface">
                                {provider.diagnostics.probe_recipe.command_env}
                              </span>
                            </p>
                          )}
                          {provider.diagnostics.probe_recipe.environment_checks
                            ?.length ? (
                            <p className="mt-1 break-words">
                              检查:{" "}
                              <span className="font-data text-on-surface">
                                {provider.diagnostics.probe_recipe.environment_checks.join(
                                  ", ",
                                )}
                              </span>
                            </p>
                          ) : null}
                        </div>
                      )}
                      {provider.diagnostics.manual_probe_command && (
                        <p className="break-words">
                          手工:{" "}
                          <span className="font-data text-on-surface">
                            {provider.diagnostics.manual_probe_command}
                          </span>
                        </p>
                      )}
                      {provider.diagnostics.troubleshooting?.[0] && (
                        <p className="leading-5">
                          {provider.diagnostics.troubleshooting[0]}
                        </p>
                      )}
                      {provider.diagnostics.startup_probe_endpoint && (
                        <div className="mt-2 flex flex-wrap gap-2">
                          <button
                            onClick={() =>
                              runProviderStartupProbe(provider.provider)
                            }
                            disabled={
                              busyAction ===
                              `provider-probe-${provider.provider}`
                            }
                            className="inline-flex items-center gap-2 rounded-lg bg-surface-container px-2.5 py-1.5 text-xs font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
                          >
                            {busyAction ===
                            `provider-probe-${provider.provider}` ? (
                              <Loader2 size={13} className="animate-spin" />
                            ) : (
                              <PlayCircle size={13} />
                            )}
                            启动探测
                          </button>
                          {provider.agent_owned &&
                            provider.command.length > 0 && (
                              <button
                                onClick={() =>
                                  runProviderTaskProbe(provider.provider)
                                }
                                disabled={
                                  busyAction ===
                                  `provider-task-probe-${provider.provider}`
                                }
                                className="inline-flex items-center gap-2 rounded-lg bg-primary px-2.5 py-1.5 text-xs font-medium text-on-primary transition-opacity hover:opacity-90 disabled:opacity-50"
                              >
                                {busyAction ===
                                `provider-task-probe-${provider.provider}` ? (
                                  <Loader2 size={13} className="animate-spin" />
                                ) : (
                                  <PlayCircle size={13} />
                                )}
                                任务探测
                              </button>
                            )}
                        </div>
                      )}
                      {providerProbeResults[provider.provider] && (
                        <div className="mt-2 rounded bg-surface-container px-2 py-1.5">
                          <p>
                            探测结果:{" "}
                            <span className="font-data text-on-surface">
                              {providerProbeResults[provider.provider].status}
                            </span>
                          </p>
                          <p className="mt-1 break-words">
                            {providerProbeResults[provider.provider].message}
                          </p>
                          {providerProbeResults[provider.provider].health
                            ?.reason && (
                            <p className="mt-1 break-words">
                              健康原因:{" "}
                              {
                                providerProbeResults[provider.provider].health
                                  ?.reason
                              }
                            </p>
                          )}
                          {providerProbeResults[provider.provider].health
                            ?.launch_kind && (
                            <p className="mt-1">
                              探测启动:{" "}
                              <span className="font-data text-on-surface">
                                {
                                  providerProbeResults[provider.provider].health
                                    ?.launch_kind
                                }
                              </span>
                              {providerProbeResults[provider.provider].health
                                ?.used_fallback && (
                                <span className="ml-2 font-medium text-warning">
                                  fallback
                                </span>
                              )}
                            </p>
                          )}
                          {providerProbeResults[provider.provider].health
                            ?.attempts && (
                            <p className="mt-1">
                              探测次数:{" "}
                              <span className="font-data text-on-surface">
                                {
                                  providerProbeResults[provider.provider].health
                                    ?.attempts?.length
                                }
                              </span>
                            </p>
                          )}
                          {(() => {
                            const attempts =
                              providerProbeResults[provider.provider].health
                                ?.attempts ?? [];
                            if (attempts.length === 0) return null;
                            return (
                              <div className="mt-2 space-y-1">
                                {attempts.slice(0, 3).map((attempt, index) => {
                                  const resolutionLines =
                                    commandResolutionLines(attempt.resolution);
                                  return (
                                    <div
                                      key={`${attempt.command ?? attempt.executable ?? index}-${index}`}
                                      className="rounded border border-outline-variant/30 px-2 py-1"
                                    >
                                      <p className="break-words font-data text-[10px] text-on-surface">
                                        attempt {index + 1}:{" "}
                                        {attempt.command ||
                                          attempt.executable ||
                                          "unknown"}{" "}
                                        {attempt.status ||
                                          attempt.probe_status ||
                                          "unknown"}
                                      </p>
                                      {(attempt.reason ||
                                        attempt.probe_message) && (
                                        <p className="mt-1 break-words">
                                          {attempt.reason ||
                                            attempt.probe_message}
                                        </p>
                                      )}
                                      {resolutionLines.length > 0 && (
                                        <div className="mt-1 space-y-0.5">
                                          {resolutionLines.map((line) => (
                                            <p
                                              key={line}
                                              className="break-words font-data text-[10px] text-on-surface"
                                            >
                                              {line}
                                            </p>
                                          ))}
                                        </div>
                                      )}
                                    </div>
                                  );
                                })}
                                {attempts.length > 3 && (
                                  <p className="font-data text-[10px]">
                                    +{attempts.length - 3} more attempts in
                                    artifact
                                  </p>
                                )}
                              </div>
                            );
                          })()}
                        </div>
                      )}
                      {providerTaskProbeResults[provider.provider] && (
                        <div className="mt-2 rounded bg-surface-container px-2 py-1.5">
                          <p>
                            任务探测:{" "}
                            <span className="font-data text-on-surface">
                              {
                                providerTaskProbeResults[provider.provider]
                                  .status
                              }
                            </span>
                            <span className="ml-2 font-data text-on-surface">
                              contract:
                              {
                                providerTaskProbeResults[provider.provider]
                                  .summary.task_contract_status
                              }
                            </span>
                          </p>
                          <p className="mt-1">
                            Execution:{" "}
                            <span className="font-data text-on-surface">
                              {
                                providerTaskProbeResults[provider.provider]
                                  .summary.execution_status
                              }
                            </span>
                            <span className="ml-2 font-data text-on-surface">
                              missing:
                              {
                                providerTaskProbeResults[provider.provider]
                                  .summary.missing_required
                              }
                            </span>
                          </p>
                          {providerTaskProbeResults[provider.provider].summary
                            .missing_artifacts.length > 0 && (
                            <p className="mt-1 break-words text-warning">
                              缺失产物:{" "}
                              {providerTaskProbeResults[
                                provider.provider
                              ].summary.missing_artifacts.join(", ")}
                            </p>
                          )}
                          <p className="mt-1 break-words font-data text-[10px]">
                            artifact:
                            {
                              providerTaskProbeResults[provider.provider]
                                .artifact.path
                            }
                          </p>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))}
              {!providerMatrix && (
                <p className="text-sm text-on-surface-variant">
                  执行器诊断会随工作台数据一起加载。
                </p>
              )}
            </div>
            {providerMatrix?.notes?.length ? (
              <div className="mt-3 flex flex-wrap gap-2">
                {providerMatrix.notes.map((note) => (
                  <span
                    key={note}
                    className="rounded bg-surface px-2 py-1 text-xs text-on-surface-variant"
                  >
                    {note}
                  </span>
                ))}
              </div>
            ) : null}
          </Panel>
        )}

        {activeWorkbenchView === "workflow" && (
          <Panel title="工作流编排" icon={<ClipboardList size={16} />}>
            <div className="mb-3 rounded-lg border border-outline-variant/30 bg-surface/82 p-2.5">
              <div className="flex flex-wrap items-center gap-2">
                {workflowPresets.length > 0 ? (
                  <select
                    value={selectedPresetId}
                    onChange={(event) => setSelectedPresetId(event.target.value)}
                    className="min-w-0 max-w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary sm:min-w-72"
                    aria-label="工作流预设"
                  >
                    {groupedWorkflowPresets.map((group) => (
                      <optgroup key={group.group} label={group.group}>
                        {group.items.map((preset) => (
                          <option key={preset.id} value={preset.id}>
                            {workflowDisplayName(preset.definition)}
                          </option>
                        ))}
                      </optgroup>
                    ))}
                  </select>
                ) : (
                  <span className="rounded-lg border border-amber-500/25 bg-amber-500/10 px-3 py-2 text-sm text-amber-700">
                    模板库暂未加载
                  </span>
                )}
                <button
                  onClick={applyPreset}
                  disabled={!selectedPresetId}
                  className="inline-flex items-center gap-2 rounded-lg bg-surface px-3 py-2 text-sm font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
                >
                  从模板库导入
                </button>
                <button
                  onClick={restoreBuiltinPresets}
                  disabled={Boolean(busyAction)}
                  className="inline-flex items-center gap-2 rounded-lg bg-surface px-3 py-2 text-sm font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
                >
                  {busyAction === "restore-builtin-presets" ? (
                    <Loader2 size={14} className="animate-spin" />
                  ) : (
                    <RefreshCw size={14} />
                  )}
                  刷新模板库
                </button>
              <button
                onClick={duplicateSelectedWorkflowDraft}
                disabled={
                  !workflows.some((item) => item.id === selectedWorkflowId)
                }
                className="inline-flex items-center gap-2 rounded-lg bg-surface px-3 py-2 text-sm font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
              >
                复制
              </button>
              <button
                onClick={saveWorkflow}
                disabled={Boolean(busyAction)}
                className="inline-flex items-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-on-primary transition-opacity hover:opacity-90 disabled:opacity-50"
              >
                {busyAction === "save-workflow" ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <Save size={14} />
                )}
                保存工作流
              </button>
              <button
                onClick={auditWorkflowDraft}
                disabled={Boolean(busyAction)}
                className="inline-flex items-center gap-2 rounded-lg bg-surface px-3 py-2 text-sm font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
              >
                {busyAction === "audit-workflow-draft" ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <Search size={14} />
                )}
                审计草稿
              </button>
                <span className="text-xs text-on-surface-variant">
                  {workflowPresets.length} 个内置模板，{workflows.length} 个已保存
                </span>
              </div>
              <p className="mt-2 text-[11px] leading-4 text-on-surface-variant">
                导入会替换当前画布草稿，不影响已保存的工作流。保存后才会出现在运行驾驶舱。
              </p>
            </div>
            <div className="ct-workflow-builder-grid grid gap-2.5 xl:grid-cols-[136px_minmax(0,1fr)_360px]">
              <aside
                aria-label="Workflow module palette"
                className="min-w-0 rounded-lg border border-outline-variant/30 bg-surface/82 p-1.5"
              >
                <div className="mb-1.5 flex items-center justify-between gap-2">
                  <p className="text-[11px] font-semibold text-on-surface">
                    节点模块
                  </p>
                  <span className="font-data text-[10px] text-on-surface-variant">
                    drag
                  </span>
                </div>
                <div className="space-y-1">
                  {WORKFLOW_MODULE_PALETTE.map((paletteModule) => (
                    <button
                      key={paletteModule.id}
                      type="button"
                      draggable
                      aria-label={paletteModule.label}
                      onDragStart={(event) => {
                        paletteDragModuleRef.current = paletteModule.id;
                        event.dataTransfer.setData(
                          "application/x-codetalk-workflow-module",
                          paletteModule.id,
                        );
                        event.dataTransfer.setData("text/plain", paletteModule.id);
                        event.dataTransfer.effectAllowed = "copy";
                      }}
                      onDragEnd={() => {
                        paletteDragModuleRef.current = null;
                      }}
                      onPointerDown={(event) =>
                        startPalettePointerDrag(paletteModule.id, event)
                      }
                      onClick={() =>
                        addPaletteNodeToCanvasViewportCenter(paletteModule.id)
                      }
                      className={[
                        "w-full rounded-md border px-1.5 py-1 text-left text-[10px] font-medium leading-tight transition-colors hover:bg-surface-container-high",
                        paletteModule.tone,
                      ].join(" ")}
                    >
                      <span className="block text-[11px] font-semibold">
                        {paletteModule.label}
                      </span>
                      <span className="mt-0.5 block truncate text-[10px] opacity-75">
                        {paletteModule.id === "input"
                          ? "repo、patch、coverage"
                          : paletteModule.id === "agent"
                            ? "Claude / OpenCode / 内置"
                            : paletteModule.id === "mcp"
                              ? "远端凭证与工具"
                              : paletteModule.id === "skills"
                                ? "AGENTS.md 与技能"
                                : paletteModule.id === "gitnexus"
                                  ? "索引、切片、证据"
                                  : paletteModule.id === "cgc"
                                    ? "调用图与结构流"
                                    : "artifact / report"}
                      </span>
                    </button>
                  ))}
                </div>
              </aside>

              <section
                aria-label="Workflow canvas"
                onDragOver={(event) => event.preventDefault()}
                className="ct-workflow-canvas min-w-0 rounded-lg border border-outline-variant/30 bg-surface/72 p-2.5"
              >
                <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                  <div className="min-w-0">
                    <p className="text-[11px] font-medium text-on-surface-variant">
                      工作区
                    </p>
                    <h3 className="truncate text-sm font-semibold text-on-surface">
                      {builderWorkflowName ||
                        workflowDisplayName(selectedWorkflowId)}
                    </h3>
                  </div>
                  <div className="flex flex-wrap gap-2 text-[11px] text-on-surface-variant">
                    <span className="rounded-md bg-surface-container px-2 py-1 font-data">
                      inputs:{workflowDraftAuditSummary.inputCount}
                    </span>
                    <span className="rounded-md bg-surface-container px-2 py-1 font-data">
                      outputs:{workflowDraftAuditSummary.outputCount}
                    </span>
                    <span className="rounded-md bg-surface-container px-2 py-1 font-data">
                      artifacts:
                      {workflowDraftAuditSummary.requiredArtifacts.length}
                    </span>
                  </div>
                </div>
                <div
                  ref={workflowBoardRef}
                  className="ct-workflow-board max-h-[720px] overflow-auto rounded-lg border border-outline-variant/20 bg-surface-container/55"
                  onDragOver={(event) => {
                    event.preventDefault();
                    event.dataTransfer.dropEffect = "copy";
                  }}
                  onDrop={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    const moduleId =
                      event.dataTransfer.getData(
                        "application/x-codetalk-workflow-module",
                      ) ||
                      event.dataTransfer.getData("text/plain") ||
                      paletteDragModuleRef.current ||
                      "";
                    paletteDragModuleRef.current = null;
                    addPaletteNodeToCanvas(
                      moduleId,
                      event.clientX,
                      event.clientY,
                    );
                  }}
                >
                  <div
                    ref={workflowCanvasInnerRef}
                    className="relative"
                    style={{
                      height: WORKFLOW_CANVAS_HEIGHT,
                      width: WORKFLOW_CANVAS_WIDTH,
                    }}
                  >
                    <svg
                      aria-hidden="true"
                      className="pointer-events-none absolute inset-0 h-full w-full text-primary/45"
                      preserveAspectRatio="none"
                      viewBox={`0 0 ${WORKFLOW_CANVAS_WIDTH} ${WORKFLOW_CANVAS_HEIGHT}`}
                    >
                      {visibleWorkflowCanvasEdges.map((edge) => {
                        const source = workflowCanvasNodes.find(
                          (node) => node.id === edge.source,
                        );
                        const target = workflowCanvasNodes.find(
                          (node) => node.id === edge.target,
                        );
                        if (!source || !target) return null;
                        const x1 = source.x + WORKFLOW_NODE_WIDTH - 12;
                        const y1 = source.y + 42;
                        const x2 = target.x;
                        const y2 = target.y + 42;
                        return (
                          <g key={edge.id}>
                            <line
                              className="ct-workflow-link"
                              x1={x1}
                              y1={y1}
                              x2={x2}
                              y2={y2}
                              stroke="currentColor"
                              strokeWidth="1.4"
                              strokeDasharray="7 5"
                            />
                            {edge.label && (
                              <text
                                x={(x1 + x2) / 2}
                                y={(y1 + y2) / 2 - 6}
                                textAnchor="middle"
                                className="fill-current font-data text-[9px]"
                              >
                                {edge.label.slice(0, 18)}
                              </text>
                            )}
                          </g>
                        );
                      })}
                    </svg>
                    <div className="relative h-full">
                      {workflowCanvasNodes.map((node, index) => (
                        <article
                          key={node.id}
                          style={{
                            left: node.x,
                            top: node.y,
                            width: WORKFLOW_NODE_WIDTH,
                          }}
                          onPointerDown={(event) =>
                            startWorkflowNodeDrag(event, node)
                          }
                          onPointerMove={moveWorkflowNode}
                          onPointerUp={endWorkflowNodeDrag}
                          onPointerCancel={endWorkflowNodeDrag}
                          className={[
                            "ct-workflow-node absolute h-24 cursor-move select-none overflow-hidden rounded-md border p-1.5 shadow-sm",
                            activeWorkflowNodeId === node.id
                              ? "ring-2 ring-primary/35"
                              : "",
                            WORKFLOW_NODE_TONE[node.kind] ??
                              "border-outline-variant/30 bg-surface",
                          ].join(" ")}
                        >
                          <div className="mb-1.5 flex items-start justify-between gap-1.5">
                            <div className="min-w-0">
                              <p className="font-data text-[9px] uppercase text-on-surface-variant">
                                node {index + 1}
                              </p>
                              <h4 className="truncate text-[11px] font-semibold text-on-surface">
                                {node.title}
                              </h4>
                              <p className="mt-0.5 truncate text-[10px] text-on-surface-variant">
                                {node.subtitle}
                              </p>
                            </div>
                            <span className="rounded bg-surface/80 px-1.5 py-0.5 font-data text-[9px] text-on-surface-variant">
                              {node.kind}
                            </span>
                          </div>
                          <div className="space-y-0.5">
                            {node.body.map((line) => (
                              <p
                                key={line}
                                className="truncate rounded bg-surface/75 px-1 py-0.5 font-data text-[9px] text-on-surface-variant"
                              >
                                {line}
                              </p>
                            ))}
                          </div>
                        </article>
                      ))}
                    </div>
                  </div>
                </div>
              </section>

              <aside
                aria-label="Workflow inspector"
                className="ct-workflow-inspector min-w-0 overflow-y-auto rounded-lg border border-outline-variant/30 bg-surface/86 p-2 [&_input]:!text-[10px] [&_select]:!text-[10px] [&_textarea]:!text-[10px]"
              >
                <div
                  data-testid="workflow-canvas-relation"
                  className="mb-2 rounded-lg border border-outline-variant/30 bg-surface-container/70 px-2 py-1.5 text-[11px] leading-4 text-on-surface-variant"
                >
                  <div className="grid gap-1 font-data">
                    <span>场景 / 字段契约 / 画布布局</span>
                    <span>场景会重置字段契约；字段契约用于生成与保存。</span>
                    <span>画布布局只保存节点位置和临时节点。</span>
                  </div>
                  <p className="mt-1 truncate text-on-surface">
                    当前节点:{activeWorkflowNode?.title ?? "未选中"} ·{" "}
                    {activeWorkflowNode?.source === "canvas"
                      ? "画布新增，未写入字段契约"
                      : "来自字段契约"}
                  </p>
                  <div className="mt-2 grid gap-1.5">
                    <label className="block">
                      <span className="mb-1 block text-[10px] text-on-surface-variant">
                        节点名称
                      </span>
                      <input
                        value={activeWorkflowNode?.title ?? ""}
                        onChange={(event) =>
                          renameActiveWorkflowNode(event.target.value)
                        }
                        disabled={!activeWorkflowNode}
                        aria-label="Workflow selected node title"
                        className="w-full rounded-md border border-outline-variant/30 bg-surface px-2 py-1 font-data text-[11px] text-on-surface outline-none focus:border-primary disabled:opacity-50"
                      />
                    </label>
                    <div className="grid grid-cols-3 gap-1">
                      <button
                        type="button"
                        onClick={copyActiveWorkflowNode}
                        disabled={!activeWorkflowNode}
                        className="inline-flex items-center justify-center gap-1 rounded-md border border-outline-variant/25 bg-surface px-1.5 py-1 text-[10px] text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
                      >
                        <Copy size={11} />
                        复制节点
                      </button>
                      <button
                        type="button"
                        onClick={deleteActiveWorkflowNode}
                        disabled={!activeWorkflowNode}
                        className="inline-flex items-center justify-center gap-1 rounded-md border border-red-400/25 bg-red-400/5 px-1.5 py-1 text-[10px] text-red-600 transition-colors hover:bg-red-400/10 disabled:opacity-50"
                      >
                        <Trash2 size={11} />
                        删除节点
                      </button>
                      <button
                        type="button"
                        onClick={resetActiveWorkflowNodePosition}
                        disabled={!activeWorkflowNode}
                        className="inline-flex items-center justify-center gap-1 rounded-md border border-outline-variant/25 bg-surface px-1.5 py-1 text-[10px] text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
                      >
                        <RotateCcw size={11} />
                        重置位置
                      </button>
                    </div>
                    <div className="grid grid-cols-[1fr_1fr_auto] gap-1.5">
                      <label className="block">
                        <span className="mb-1 block text-[10px] text-on-surface-variant">
                          从
                        </span>
                        <select
                          aria-label="Workflow link source"
                          value={workflowLinkSourceId || activeWorkflowNode?.id || ""}
                          onChange={(event) => {
                            setWorkflowLinkSourceId(event.target.value);
                            setActiveWorkflowNodeId(event.target.value);
                            if (event.target.value === workflowLinkTargetId) {
                              setWorkflowLinkTargetId("");
                            }
                          }}
                          className="w-full rounded-md border border-outline-variant/30 bg-surface px-2 py-1 text-[11px] text-on-surface outline-none focus:border-primary disabled:opacity-50"
                        >
                          {workflowCanvasNodes.map((node) => (
                            <option key={node.id} value={node.id}>
                              {node.title}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="block">
                        <span className="mb-1 block text-[10px] text-on-surface-variant">
                          到
                        </span>
                        <select
                          aria-label="Workflow link target"
                          value={workflowLinkTargetId}
                          onChange={(event) =>
                            setWorkflowLinkTargetId(event.target.value)
                          }
                          disabled={!activeWorkflowNode}
                          className="w-full rounded-md border border-outline-variant/30 bg-surface px-2 py-1 text-[11px] text-on-surface outline-none focus:border-primary disabled:opacity-50"
                        >
                          <option value="">选择目标节点</option>
                          {workflowCanvasNodes
                            .filter(
                              (node) =>
                                node.id !==
                                (workflowLinkSourceId ||
                                  activeWorkflowNode?.id ||
                                  ""),
                            )
                            .map((node) => (
                              <option key={node.id} value={node.id}>
                                {node.title}
                              </option>
                            ))}
                        </select>
                      </label>
                      <button
                        type="button"
                        onClick={connectActiveWorkflowNode}
                        disabled={
                          !(workflowLinkSourceId || activeWorkflowNode) ||
                          !workflowLinkTargetId
                        }
                        className="mt-5 inline-flex items-center justify-center rounded-md border border-outline-variant/25 bg-surface px-2 py-1 text-[10px] text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
                      >
                        连接节点
                      </button>
                    </div>
                  </div>
                </div>
                {groupedWorkflowPresets.length > 0 && (
                  <details className="mb-3 rounded-lg border border-outline-variant/30 bg-surface-container/70 p-2">
                    <summary className="cursor-pointer text-xs font-semibold text-on-surface">
                      预设库
                    </summary>
                    <div className="mt-2 space-y-2">
                      {groupedWorkflowPresets.map((group) => (
                        <div key={group.group}>
                          <div className="mb-1 flex items-center justify-between gap-2">
                            <p className="text-xs font-medium text-on-surface-variant">
                              {group.group}
                            </p>
                            <span className="font-data text-[10px] text-on-surface-variant">
                              {group.items.length}
                            </span>
                          </div>
                          <div className="flex flex-wrap gap-1.5">
                            {group.items
                              .slice(0, group.group === "核心工作流" ? 8 : 10)
                              .map((preset) => (
                                <button
                                  key={preset.id}
                                  type="button"
                                  onClick={() => {
                                    setSelectedPresetId(preset.id);
                                    setWorkflowJson(pretty(preset.definition));
                                    setSelectedWorkflowId(preset.definition.id);
                                  }}
                                  className={[
                                    "max-w-full rounded-md border px-2 py-1 text-left text-[11px] transition-colors",
                                    selectedPresetId === preset.id
                                      ? "border-primary/40 bg-primary text-on-primary"
                                      : "border-outline-variant/30 bg-surface text-on-surface hover:bg-surface-container-high",
                                  ].join(" ")}
                                  title={preset.description}
                                >
                                  {workflowDisplayName(preset.definition)}
                                </button>
                              ))}
                            {group.items.length >
                              (group.group === "核心工作流" ? 8 : 10) && (
                              <span className="rounded-md border border-outline-variant/20 px-2 py-1 text-[11px] text-on-surface-variant">
                                +
                                {group.items.length -
                                  (group.group === "核心工作流" ? 8 : 10)}
                              </span>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  </details>
                )}

                <div className="mb-3 rounded-lg border border-outline-variant/30 bg-surface-container/70 p-2">
                  <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                    <p className="text-sm font-medium text-on-surface">
                      AI 生成工作流
                    </p>
                    <button
                      onClick={generateAiWorkflowDraft}
                      disabled={
                        Boolean(busyAction) ||
                        aiWorkflowPrompt.trim().length < 8
                      }
                      className="inline-flex items-center gap-2 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-on-primary transition-opacity hover:opacity-90 disabled:opacity-50"
                    >
                      {busyAction === "generate-ai-workflow" ? (
                        <Loader2 size={13} className="animate-spin" />
                      ) : (
                        <WandSparkles size={13} />
                      )}
                      AI 生成
                    </button>
                  </div>
                  <div className="grid gap-2">
                    <label className="block">
                      <span className="mb-1 block text-xs text-on-surface-variant">
                        偏好 ID
                      </span>
                      <input
                        value={aiWorkflowPreferredId}
                        onChange={(event) =>
                          setAiWorkflowPreferredId(event.target.value)
                        }
                        className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 font-data text-xs text-on-surface outline-none focus:border-primary"
                        aria-label="AI workflow preferred id"
                      />
                    </label>
                    <label className="block">
                      <span className="mb-1 block text-xs text-on-surface-variant">
                        偏好名称
                      </span>
                      <input
                        value={aiWorkflowPreferredName}
                        onChange={(event) =>
                          setAiWorkflowPreferredName(event.target.value)
                        }
                        className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                        aria-label="AI workflow preferred name"
                      />
                    </label>
                  </div>
                  <label className="mt-2 block">
                    <span className="mb-1 block text-xs text-on-surface-variant">
                      工作流话术
                    </span>
                    <textarea
                      value={aiWorkflowPrompt}
                      onChange={(event) =>
                        setAiWorkflowPrompt(event.target.value)
                      }
                      className="h-20 w-full resize-y rounded-lg border border-outline-variant/30 bg-surface p-3 text-xs text-on-surface outline-none focus:border-primary"
                      aria-label="AI workflow prompt"
                    />
                  </label>
                  {aiWorkflowGeneration && (
                    <div className="mt-2 rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-xs text-on-surface-variant">
                      <div className="flex flex-wrap gap-2">
                        <span className="font-medium text-on-surface">
                          generation:{aiWorkflowGeneration.generation_id}
                        </span>
                        <span>audit:{aiWorkflowGeneration.audit.status}</span>
                        <span>
                          warnings:{aiWorkflowGeneration.audit.warnings.length}
                        </span>
                        {aiWorkflowGeneration.artifact?.path && (
                          <span className="break-all">
                            artifact:{aiWorkflowGeneration.artifact.path}
                          </span>
                        )}
                      </div>
                    </div>
                  )}
                </div>

                <div className="rounded-lg border border-outline-variant/30 bg-surface-container/70 p-2">
                  <div className="mb-3 flex flex-wrap items-end gap-2">
                    <label className="min-w-0 flex-1">
                      <span className="mb-1 block text-xs text-on-surface-variant">
                        场景
                      </span>
                      <select
                        value={builderScenario}
                        onChange={(event) =>
                          applyBuilderScenario(
                            event.target
                              .value as keyof typeof WORKFLOW_BUILDER_SCENARIOS,
                          )
                        }
                        className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                        aria-label="Workflow builder scenario"
                      >
                        {Object.entries(WORKFLOW_BUILDER_SCENARIOS).map(
                          ([id, scenario]) => (
                            <option key={id} value={id}>
                              {scenario.name}
                            </option>
                          ),
                        )}
                      </select>
                    </label>
                    <button
                      onClick={generateWorkflowDraft}
                      disabled={Boolean(busyAction)}
                      className="inline-flex items-center gap-2 rounded-lg bg-surface px-3 py-2 text-sm font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
                    >
                      {busyAction === "generate-workflow" ? (
                        <Loader2 size={14} className="animate-spin" />
                      ) : (
                        <ClipboardList size={14} />
                      )}
                      生成草稿
                    </button>
                  </div>
                  <div className="grid gap-2">
                    <label className="block">
                      <span className="mb-1 block text-xs text-on-surface-variant">
                        工作流 ID
                      </span>
                      <input
                        value={builderWorkflowId}
                        onChange={(event) =>
                          setBuilderWorkflowId(event.target.value)
                        }
                        className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                        aria-label="Workflow builder id"
                      />
                    </label>
                    <label className="block">
                      <span className="mb-1 block text-xs text-on-surface-variant">
                        工作流名称
                      </span>
                      <input
                        value={builderWorkflowName}
                        onChange={(event) =>
                          setBuilderWorkflowName(event.target.value)
                        }
                        className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                        aria-label="Workflow builder name"
                      />
                    </label>
                    <label className="block">
                      <span className="mb-1 block text-xs text-on-surface-variant">
                        执行器预设
                      </span>
                      <select
                        value={
                          builderProviderOptions.some(
                            (provider) => provider.id === builderProvider,
                          )
                            ? builderProvider
                            : ""
                        }
                        onChange={(event) => {
                          if (event.target.value) {
                            setBuilderProvider(event.target.value);
                          }
                        }}
                        className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                        aria-label="Workflow builder provider preset"
                      >
                        <option value="">自定义执行器</option>
                        {builderProviderOptions.map((provider) => (
                          <option key={provider.id} value={provider.id}>
                            {provider.label} ({provider.id}:{provider.status})
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="block">
                      <span className="mb-1 block text-xs text-on-surface-variant">
                        执行器 ID
                      </span>
                      <input
                        value={builderProvider}
                        onChange={(event) =>
                          setBuilderProvider(event.target.value)
                        }
                        className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                        aria-label="Workflow builder provider"
                      />
                    </label>
                    <label className="block">
                      <span className="mb-1 block text-xs text-on-surface-variant">
                        MCP 配置
                      </span>
                      <select
                        value={builderMcpProfile}
                        onChange={(event) => {
                          setBuilderMcpProfile(event.target.value);
                        }}
                        className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                        aria-label="Workflow builder MCP 配置"
                      >
                        <option value="">不启用 MCP</option>
                        {builderMcpOptions.map((profile) => (
                          <option key={profile} value={profile}>
                            {profile}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="block">
                      <span className="mb-1 block text-xs text-on-surface-variant">
                        自定义 MCP profile
                      </span>
                      <input
                        value={builderMcpProfile}
                        onChange={(event) =>
                          setBuilderMcpProfile(event.target.value)
                        }
                        placeholder="例如 codehub-readonly"
                        className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                        aria-label="Workflow builder custom MCP profile"
                      />
                    </label>
                  </div>
                  <div
                    className={[
                      "mt-2 rounded-lg border px-3 py-2 text-xs",
                      builderMcpCompatibility.level === "ok"
                        ? "border-emerald-400/25 bg-emerald-400/8 text-emerald-700"
                        : builderMcpCompatibility.level === "fallback"
                          ? "border-sky-400/25 bg-sky-400/8 text-sky-700"
                          : builderMcpCompatibility.level === "warn"
                            ? "border-amber-400/30 bg-amber-400/10 text-amber-700"
                            : "border-outline-variant/30 bg-surface-container text-on-surface-variant",
                    ].join(" ")}
                    aria-label="Workflow builder MCP compatibility"
                  >
                    <div className="font-medium">
                      {builderMcpCompatibility.label}
                    </div>
                    <div className="mt-0.5">
                      {builderMcpCompatibility.detail}
                    </div>
                  </div>
                  <div className="mt-2 rounded-lg border border-outline-variant/30 bg-surface px-3 py-2">
                    <div className="mb-2 flex items-center justify-between gap-2">
                      <span className="text-xs font-medium text-on-surface">
                        Skills
                      </span>
                      <button
                        type="button"
                        onClick={() => setBuilderSkillIds(DEFAULT_BUILDER_SKILL_IDS)}
                        className="rounded-md bg-surface-container px-2 py-1 text-[11px] text-on-surface-variant transition-colors hover:bg-surface-container-high"
                      >
                        恢复默认
                      </button>
                    </div>
                    <div className="mb-2 grid gap-1.5">
                      <input
                        aria-label="Workflow builder skill search"
                        value={builderSkillQuery}
                        onChange={(event) =>
                          setBuilderSkillQuery(event.target.value)
                        }
                        placeholder="搜索 skill 名称、来源或用途"
                        className="w-full rounded-md border border-outline-variant/30 bg-surface-container px-2 py-1 font-data text-[10px] text-on-surface outline-none focus:border-primary"
                      />
                      <div className="flex flex-wrap gap-1.5 text-[10px] text-on-surface-variant">
                        <span className="rounded bg-surface-container px-1.5 py-0.5">
                          已选 {builderSkillIds.length}
                        </span>
                        <span
                          aria-label="Workflow builder visible skill count"
                          className="rounded bg-surface-container px-1.5 py-0.5"
                        >
                          显示 {visibleBuilderSkillOptions.length}/
                          {workflowSkillOptions.length}
                        </span>
                        {!builderSkillQuery.trim() &&
                          workflowSkillOptions.length >
                            visibleBuilderSkillOptions.length && (
                            <span className="rounded bg-surface-container px-1.5 py-0.5">
                              输入关键词查看更多
                            </span>
                          )}
                      </div>
                    </div>
                    <div
                      className="grid gap-1.5"
                      aria-label="Workflow builder skills"
                    >
                      {visibleBuilderSkillOptions.map((skill) => {
                        const checked = builderSkillIds.includes(skill.id);
                        return (
                          <label
                            key={skill.id}
                            className="flex items-start gap-2 rounded-md border border-outline-variant/20 bg-surface-container/50 px-2 py-1.5 text-xs text-on-surface-variant"
                          >
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={(event) => {
                                setBuilderSkillIds((current) =>
                                  event.target.checked
                                    ? uniqueWorkflowStrings([...current, skill.id])
                                    : current.filter((id) => id !== skill.id),
                                );
                              }}
                              className="mt-0.5"
                              aria-label={`Workflow builder skill ${skill.id}`}
                            />
                            <span className="min-w-0">
                              <span className="block font-medium text-on-surface">
                                {skill.label}
                              </span>
                              {skill.description && (
                                <span className="block text-[11px] leading-snug">
                                  {skill.description}
                                </span>
                              )}
                            </span>
                          </label>
                        );
                      })}
                    </div>
                  </div>
                  <div className="mt-2 rounded-lg border border-outline-variant/30 bg-surface-container/65 p-2">
                    <div className="mb-2 flex items-center justify-between gap-2">
                      <p className="text-xs font-medium text-on-surface">输入契约</p>
                      <span className="font-data text-[10px] text-on-surface-variant">
                        {builderInputItems.length}
                      </span>
                    </div>
                    <div className="mb-2 flex flex-wrap gap-1">
                      {builderInputItems.map((item) => (
                        <span
                          key={item.id}
                          className="rounded bg-surface px-1.5 py-1 font-data text-[10px] text-on-surface-variant"
                        >
                          {workflowItemLabel(builderInputLabels, item.id)}
                          <span className="text-on-surface-variant/70">
                            {" "}
                            {item.id}:{item.type}
                          </span>
                        </span>
                      ))}
                    </div>
                    <div className="grid grid-cols-2 gap-1.5">
                      <input
                        aria-label="New workflow input name"
                        value={newWorkflowInputName}
                        onChange={(event) =>
                          setNewWorkflowInputName(event.target.value)
                        }
                        placeholder="输入名称，如 MR 链接"
                        className="rounded-md border border-outline-variant/30 bg-surface px-2 py-1 text-[11px] text-on-surface outline-none focus:border-primary"
                      />
                      <input
                        aria-label="New workflow input id"
                        value={newWorkflowInputId}
                        onChange={(event) =>
                          setNewWorkflowInputId(event.target.value)
                        }
                        placeholder="input_id"
                        className="rounded-md border border-outline-variant/30 bg-surface px-2 py-1 font-data text-[11px] text-on-surface outline-none focus:border-primary"
                      />
                      <select
                        aria-label="New workflow input type"
                        value={newWorkflowInputType}
                        onChange={(event) =>
                          setNewWorkflowInputType(event.target.value)
                        }
                        className="rounded-md border border-outline-variant/30 bg-surface px-2 py-1 text-[11px] text-on-surface outline-none focus:border-primary"
                      >
                        <option value="directory">源码目录</option>
                        <option value="file">单个文件</option>
                        <option value="file_set">多个文件</option>
                        <option value="mr_link">MR 链接</option>
                        <option value="coverage_report">覆盖率报告</option>
                        <option value="long_text">长文本</option>
                        <option value="free_text">短文本</option>
                      </select>
                      <select
                        aria-label="New workflow input resolver"
                        value={newWorkflowInputResolver}
                        onChange={(event) =>
                          setNewWorkflowInputResolver(event.target.value)
                        }
                        className="rounded-md border border-outline-variant/30 bg-surface px-2 py-1 text-[11px] text-on-surface outline-none focus:border-primary"
                      >
                        <option value="manual">用户选择/填写</option>
                        <option value="agent_mcp">Agent 通过 MCP 解析</option>
                        <option value="local">本地路径</option>
                      </select>
                    </div>
                    <button
                      type="button"
                      onClick={addBuilderInputContract}
                      className="mt-1.5 rounded-md bg-surface px-2 py-1 text-[11px] font-medium text-on-surface transition-colors hover:bg-surface-container-high"
                    >
                      添加输入契约
                    </button>
                  </div>
                  <div className="mt-2 rounded-lg border border-outline-variant/30 bg-surface-container/65 p-2">
                    <div className="mb-2 flex items-center justify-between gap-2">
                      <p className="text-xs font-medium text-on-surface">输出契约</p>
                      <span className="font-data text-[10px] text-on-surface-variant">
                        {builderOutputItems.length}
                      </span>
                    </div>
                    <div className="mb-2 flex flex-wrap gap-1">
                      {builderOutputItems.map((item) => (
                        <span
                          key={item.id}
                          className="rounded bg-surface px-1.5 py-1 font-data text-[10px] text-on-surface-variant"
                        >
                          {workflowItemLabel(builderOutputLabels, item.id)}
                          <span className="text-on-surface-variant/70">
                            {" "}
                            {item.artifact || item.type}
                          </span>
                        </span>
                      ))}
                    </div>
                    <div className="grid grid-cols-2 gap-1.5">
                      <input
                        aria-label="New workflow output name"
                        value={newWorkflowOutputName}
                        onChange={(event) =>
                          setNewWorkflowOutputName(event.target.value)
                        }
                        placeholder="输出名称，如 SFMEA 表"
                        className="rounded-md border border-outline-variant/30 bg-surface px-2 py-1 text-[11px] text-on-surface outline-none focus:border-primary"
                      />
                      <input
                        aria-label="New workflow output id"
                        value={newWorkflowOutputId}
                        onChange={(event) =>
                          setNewWorkflowOutputId(event.target.value)
                        }
                        placeholder="output_id"
                        className="rounded-md border border-outline-variant/30 bg-surface px-2 py-1 font-data text-[11px] text-on-surface outline-none focus:border-primary"
                      />
                      <select
                        aria-label="New workflow output type"
                        value={newWorkflowOutputType}
                        onChange={(event) =>
                          setNewWorkflowOutputType(event.target.value)
                        }
                        className="rounded-md border border-outline-variant/30 bg-surface px-2 py-1 text-[11px] text-on-surface outline-none focus:border-primary"
                      >
                        <option value="json">JSON 表</option>
                        <option value="test_cases">测试用例</option>
                        <option value="scope_report">分析报告</option>
                        <option value="markdown">Markdown</option>
                      </select>
                      <input
                        aria-label="New workflow output artifact"
                        value={newWorkflowOutputArtifact}
                        onChange={(event) =>
                          setNewWorkflowOutputArtifact(event.target.value)
                        }
                        placeholder="output.json"
                        className="rounded-md border border-outline-variant/30 bg-surface px-2 py-1 font-data text-[11px] text-on-surface outline-none focus:border-primary"
                      />
                    </div>
                    <button
                      type="button"
                      onClick={addBuilderOutputContract}
                      className="mt-1.5 rounded-md bg-surface px-2 py-1 text-[11px] font-medium text-on-surface transition-colors hover:bg-surface-container-high"
                    >
                      添加输出契约
                    </button>
                  </div>
                  <details className="mt-2 rounded-lg border border-outline-variant/30 bg-surface-container/65 p-2">
                    <summary className="cursor-pointer text-xs font-medium text-on-surface">
                      高级 DSL / Schema
                    </summary>
                  <label className="mt-2 block">
                    <span className="mb-1 block text-xs text-on-surface-variant">
                      输入项，格式 id:type 或 id:type@resolver
                    </span>
                    <input
                      value={builderInputSpec}
                      onChange={(event) =>
                        setBuilderInputSpec(event.target.value)
                      }
                      className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 font-data text-xs text-on-surface outline-none focus:border-primary"
                      aria-label="Workflow builder inputs"
                    />
                  </label>
                  <label className="mt-2 block">
                    <span className="mb-1 block text-xs text-on-surface-variant">
                      输出项，格式 id:type 或 id:type=artifact
                    </span>
                    <input
                      value={builderOutputSpec}
                      onChange={(event) =>
                        setBuilderOutputSpec(event.target.value)
                      }
                      className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 font-data text-xs text-on-surface outline-none focus:border-primary"
                      aria-label="Workflow builder outputs"
                    />
                  </label>
                  <label className="mt-2 block">
                    <span className="mb-1 block text-xs text-on-surface-variant">
                      必需产物
                    </span>
                    <input
                      value={builderArtifacts}
                      onChange={(event) =>
                        setBuilderArtifacts(event.target.value)
                      }
                      className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 font-data text-xs text-on-surface outline-none focus:border-primary"
                      aria-label="Workflow builder required artifacts"
                    />
                  </label>
                  <label className="mt-2 block">
                    <span className="mb-1 block text-xs text-on-surface-variant">
                      输出 Schema JSON
                    </span>
                    <textarea
                      value={builderOutputSchemas}
                      onChange={(event) =>
                        setBuilderOutputSchemas(event.target.value)
                      }
                      className="h-24 w-full resize-y rounded-lg border border-outline-variant/30 bg-surface p-3 font-data text-xs text-on-surface outline-none focus:border-primary"
                      aria-label="Workflow builder output schemas"
                      spellCheck={false}
                    />
                  </label>
                  <label className="mt-2 block">
                    <span className="mb-1 block text-xs text-on-surface-variant">
                      证据映射 JSON
                    </span>
                    <textarea
                      value={builderEvidenceMappings}
                      onChange={(event) =>
                        setBuilderEvidenceMappings(event.target.value)
                      }
                      className="h-28 w-full resize-y rounded-lg border border-outline-variant/30 bg-surface p-3 font-data text-xs text-on-surface outline-none focus:border-primary"
                      aria-label="Workflow builder evidence mappings"
                      spellCheck={false}
                    />
                  </label>
                  <label className="mt-2 block">
                    <span className="mb-1 block text-xs text-on-surface-variant">
                      语义导入 JSON
                    </span>
                    <textarea
                      value={builderSemanticImports}
                      onChange={(event) =>
                        setBuilderSemanticImports(event.target.value)
                      }
                      className="h-20 w-full resize-y rounded-lg border border-outline-variant/30 bg-surface p-3 font-data text-xs text-on-surface outline-none focus:border-primary"
                      aria-label="Workflow builder semantic imports"
                      spellCheck={false}
	                    />
	                  </label>
                  </details>
	                  {builderOutputPreview.length > 0 && (
                    <div className="mt-2 rounded-lg border border-outline-variant/30 bg-surface px-2 py-1.5">
                      <p className="mb-1 text-xs font-medium text-on-surface-variant">
                        输出契约预览
                      </p>
                      <div className="space-y-1 font-data text-[10px] text-on-surface-variant">
                        {builderOutputPreview.map((output) => (
                          <div
                            key={output.id + ":" + output.type}
                            className="break-words rounded bg-surface-container px-1.5 py-1"
                          >
                            <span className="text-on-surface">
                              {output.id}:{output.type}
                            </span>
                            {output.artifact && (
                              <span> artifact:{output.artifact}</span>
                            )}
                            {output.schema && <span> schema</span>}
                            {output.evidenceMemory && (
                              <span>
                                {" "}
                                evidence_memory
                                {output.evidenceKind
                                  ? ":" + output.evidenceKind
                                  : ""}
                              </span>
                            )}
                            {output.semanticImport && (
                              <span> semantic_import</span>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  <label className="mt-2 block">
                    <span className="mb-1 block text-xs text-on-surface-variant">
                      输入 Schema JSON
                    </span>
                    <textarea
                      value={builderInputSchemas}
                      onChange={(event) =>
                        setBuilderInputSchemas(event.target.value)
                      }
                      className="h-24 w-full resize-y rounded-lg border border-outline-variant/30 bg-surface p-3 font-data text-xs text-on-surface outline-none focus:border-primary"
                      aria-label="Workflow builder input schemas"
                      spellCheck={false}
                    />
	                  </label>
                  <label className="mt-2 block">
                    <span className="mb-1 block text-xs text-on-surface-variant">
                      智能体目标
                    </span>
                    <textarea
                      value={builderGoal}
                      onChange={(event) => setBuilderGoal(event.target.value)}
                      className="h-20 w-full resize-y rounded-lg border border-outline-variant/30 bg-surface p-3 text-xs text-on-surface outline-none focus:border-primary"
                      aria-label="Workflow builder goal"
                    />
                  </label>
                </div>

                <div
                  className={[
                    "mt-3 rounded-lg border px-3 py-2 text-xs",
                    workflowDraftAuditSummary.status === "invalid"
                      ? "border-red-400/20 bg-red-400/5 text-red-300"
                      : workflowDraftAuditSummary.status === "warning"
                        ? "border-amber-400/20 bg-amber-400/5 text-amber-300"
                        : "border-outline-variant/30 bg-surface-container text-on-surface-variant",
                  ].join(" ")}
                >
                  <div className="flex flex-wrap gap-2">
                    <span className="font-medium">
                      Draft:{workflowDraftAuditSummary.status}
                    </span>
                    <span>inputs:{workflowDraftAuditSummary.inputCount}</span>
                    <span>steps:{workflowDraftAuditSummary.stepCount}</span>
                    <span>
                      agent:{workflowDraftAuditSummary.agentStepCount}
                    </span>
                    <span>outputs:{workflowDraftAuditSummary.outputCount}</span>
                    <span>
                      evidence:
                      {workflowDraftAuditSummary.evidenceMemoryOutputCount}
                    </span>
                    <span>
                      semantic:
                      {workflowDraftAuditSummary.semanticImportOutputCount}
                    </span>
                    <span>
                      artifacts:
                      {workflowDraftAuditSummary.requiredArtifacts.length}
                    </span>
                  </div>
                  {workflowDraftAuditSummary.blocking.length > 0 && (
                    <div className="mt-1 space-y-0.5 font-data text-[10px]">
                      {workflowDraftAuditSummary.blocking
                        .slice(0, 3)
                        .map((item) => (
                          <div key={item}>blocking:{item}</div>
                        ))}
                    </div>
                  )}
                  {workflowDraftAuditSummary.warnings.length > 0 && (
                    <div className="mt-1 space-y-0.5 font-data text-[10px]">
                      {workflowDraftAuditSummary.warnings
                        .slice(0, 3)
                        .map((item) => (
                          <div key={item}>warning:{item}</div>
                        ))}
                    </div>
                  )}
                </div>
                {workflowDraftServerAudit && (
                  <div
                    className={[
                      "mt-2 rounded-lg border px-3 py-2 text-xs",
                      workflowDraftServerAudit.valid
                        ? workflowDraftServerAudit.status === "warning"
                          ? "border-amber-400/20 bg-amber-400/5 text-amber-300"
                          : "border-outline-variant/30 bg-surface-container text-on-surface-variant"
                        : "border-red-400/20 bg-red-400/5 text-red-300",
                    ].join(" ")}
                  >
                    <div className="flex flex-wrap gap-2">
                      <span className="font-medium">
                        Server audit:{workflowDraftServerAudit.status}
                      </span>
                      <span>
                        valid:{String(workflowDraftServerAudit.valid)}
                      </span>
                      <span>
                        warnings:{workflowDraftServerAudit.warnings.length}
                      </span>
                    </div>
                    {workflowDraftServerAudit.error && (
                      <div className="mt-1 break-words font-data text-[10px]">
                        error:{workflowDraftServerAudit.error}
                      </div>
                    )}
                    {workflowDraftServerAudit.warnings.length > 0 && (
                      <div className="mt-1 space-y-0.5 font-data text-[10px]">
                        {workflowDraftServerAudit.warnings
                          .slice(0, 4)
                          .map((warning) => (
                            <div
                              key={warning.code + ":" + warning.path}
                              className="break-words"
                            >
                              {warning.code}:{warning.message}
                            </div>
                          ))}
                      </div>
                    )}
                  </div>
                )}
                <details className="mt-3 rounded-lg border border-outline-variant/30 bg-surface-container/70 p-2">
                  <summary className="cursor-pointer text-xs font-medium text-on-surface">
                    高级 Workflow JSON
                  </summary>
                  <textarea
                    value={workflowJson}
                    onChange={(event) => setWorkflowJson(event.target.value)}
                    className="mt-2 h-64 max-h-[42vh] w-full resize-y rounded-lg border border-outline-variant/30 bg-surface p-3 font-data text-xs text-on-surface outline-none focus:border-primary"
                    aria-label="Workflow JSON"
                    spellCheck={false}
                  />
                </details>
              </aside>
            </div>
          </Panel>
        )}

        {activeWorkbenchView === "run" && (
          <Panel title="任务运行" icon={<PlayCircle size={16} />}>
            <div className="grid gap-4 xl:grid-cols-[minmax(380px,0.95fr)_minmax(440px,1.05fr)] xl:items-start">
              <div className="min-w-0 space-y-3">
              <label className="block">
                <span className="mb-1 block text-xs text-on-surface-variant">
                  工作流
                </span>
                <select
                  aria-label="工作流"
                  value={selectedWorkflowId}
                  onChange={(event) =>
                    selectRunWorkflow(event.target.value)
                  }
                  className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                >
                  {[selectedWorkflowId, ...workflowOptions]
                    .map((item) =>
                      typeof item === "string"
                        ? { id: item, label: workflowDisplayName(item) }
                        : item,
                    )
                    .filter(
                      (option, index, options) =>
                        option.id &&
                        options.findIndex((item) => item.id === option.id) ===
                          index,
                    )
                    .map((option) => (
                      <option key={option.id} value={option.id}>
                        {option.label}
                      </option>
                    ))}
                </select>
              </label>
              {selectedWorkflowAudit &&
                selectedWorkflowAudit.warnings.length > 0 && (
                  <div className="rounded-lg border border-amber-400/20 bg-amber-400/5 px-3 py-2 text-xs text-amber-300">
                    <div className="flex items-start gap-2">
                      <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                      <div className="min-w-0">
                        <p className="font-medium">
                          工作流审计警告:{" "}
                          {selectedWorkflowAudit.warnings.length}
                        </p>
                        <div className="mt-1 space-y-1">
                          {selectedWorkflowAudit.warnings
                            .slice(0, 3)
                            .map((warning) => (
                              <p
                                key={`${warning.code}-${warning.path}`}
                                className="break-words"
                              >
                                {warning.code}: {warning.message}
                              </p>
                            ))}
                        </div>
                      </div>
                    </div>
                  </div>
                )}
              <div className="rounded-lg border border-outline-variant/30 bg-surface-container/70 p-3">
                <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                  <p className="text-xs font-semibold text-on-surface">
                    测试活动运行摘要
                  </p>
                  <span className="rounded bg-surface px-1.5 py-0.5 font-data text-[10px] text-on-surface-variant">
                    {workflowDisplayName(selectedWorkflowId)}
                  </span>
                </div>
                <div className="grid gap-1.5 font-data text-[11px] text-on-surface-variant sm:grid-cols-2">
                  <span className="break-words">
                    工作空间: {workspaceId || "未选择"}
                  </span>
                  <span className="break-words">
                    源码路径: {repoPath.trim() || "未填写"}
                  </span>
                  <span>
                    执行器: {selectedRunProvider}
                    {selectedProviderCapability
                      ? ` (${selectedProviderCapability.status})`
                      : ""}
                  </span>
                  <span>MCP: {selectedRunMcpProfile}</span>
                  <span>
                    输入: {filledInputCount}/{selectedWorkflowInputs.length}
                    {requiredInputCount ? ` · 必填 ${requiredInputCount}` : ""}
                  </span>
                  <span>Agent 步骤: {selectedAgentStep ? "1" : "0"}</span>
                </div>
                <div className="mt-2 rounded-md border border-outline-variant/20 bg-surface px-2 py-1.5">
                  <p className="mb-1 text-[11px] font-medium text-on-surface">
                    输出预期
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {selectedWorkflowOutputs.length > 0 ? (
                      selectedWorkflowOutputs.slice(0, 8).map((output) => (
                        <span
                          key={`${String(output.id ?? "output")}:${String(output.artifact ?? "")}`}
                          className="rounded bg-surface-container px-1.5 py-0.5 font-data text-[10px] text-on-surface-variant"
                        >
                          {workflowOutputDisplayName(output)}
                          {output.artifact
                            ? ` -> ${artifactShortName(String(output.artifact))}`
                            : ""}
                        </span>
                      ))
                    ) : (
                      <span className="text-[11px] text-on-surface-variant">
                        尚未声明输出
                      </span>
                    )}
                  </div>
                </div>
                <div
                  aria-label="Run constraints"
                  className="mt-2 rounded-md border border-outline-variant/20 bg-surface px-2 py-1.5"
                >
                  <p className="mb-1 text-[11px] font-medium text-on-surface">
                    运行约束
                  </p>
                  <div className="grid gap-1.5 text-[10px] text-on-surface-variant sm:grid-cols-2">
                    <span className="rounded bg-surface-container px-1.5 py-0.5 font-data">
                      MCP: {selectedRunMcpProfile || "未启用"}
                    </span>
                    <span className="rounded bg-surface-container px-1.5 py-0.5 font-data">
                      Agent: {selectedRunProvider}
                    </span>
                  </div>
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {selectedAgentSkillInstructions.length > 0 ? (
                      selectedAgentSkillInstructions.slice(0, 8).map((skill) => (
                        <span
                          key={String(skill.id ?? skill.label ?? "skill")}
                          className="rounded bg-surface-container px-1.5 py-0.5 text-[10px] text-on-surface-variant"
                        >
                          {String(skill.label ?? skill.id ?? "skill")}
                        </span>
                      ))
                    ) : selectedAgentSkillIds.length > 0 ? (
                      selectedAgentSkillIds.slice(0, 8).map((skillId) => (
                        <span
                          key={skillId}
                          className="rounded bg-surface-container px-1.5 py-0.5 font-data text-[10px] text-on-surface-variant"
                        >
                          {skillId}
                        </span>
                      ))
                    ) : (
                      <span className="text-[11px] text-on-surface-variant">
                        未声明 skills
                      </span>
                    )}
                  </div>
                </div>
                <div className="mt-2 rounded-md border border-outline-variant/20 bg-surface px-2 py-1.5">
                  <p className="mb-1 text-[11px] font-medium text-on-surface">
                    运行前检查
                  </p>
                  <div className="grid gap-1 font-data text-[10px] text-on-surface-variant sm:grid-cols-3">
                    <span
                      className={
                        repoPath.trim() ? "text-on-surface" : "text-warning"
                      }
                    >
                      源码路径:{repoPath.trim() ? "ready" : "missing"}
                    </span>
                    <span
                      className={
                        filledInputCount >= requiredInputCount
                          ? "text-on-surface"
                          : "text-warning"
                      }
                    >
                      输入契约:{filledInputCount}/{requiredInputCount}
                    </span>
                    <span
                      className={
                        selectedProviderCapability?.status === "available" ||
                        selectedProviderCapability?.status === "configured"
                          ? "text-on-surface"
                          : "text-warning"
                      }
                    >
                      执行器:
                      {selectedProviderCapability?.status ?? "待探测"}
                    </span>
                  </div>
                </div>
              </div>
              <label className="block">
                <span className="mb-1 block text-xs text-on-surface-variant">
                  工作空间
                </span>
                {workspaces.length > 0 ? (
                  <select
                    aria-label="Workspace selector"
                    value={
                      workspaces.some(
                        (workspace) => workspace.id === workspaceId,
                      )
                        ? workspaceId
                        : ""
                    }
                    onChange={(event) => {
                      const workspace = workspaces.find(
                        (item) => item.id === event.target.value,
                      );
                      if (workspace) applyWorkspaceSelection(workspace);
                    }}
                    className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                  >
                    <option value="" disabled>
                      请选择已创建工作空间
                    </option>
                    {workspaces.map((workspace) => (
                      <option key={workspace.id} value={workspace.id}>
                        {workspace.name} · {workspace.repo_path}
                      </option>
                    ))}
                  </select>
                ) : (
                  <div className="rounded-lg border border-warning/25 bg-warning/5 px-3 py-2 text-xs text-warning">
                    还没有可运行的工作空间，请先在工作空间页面创建并完成索引。
                  </div>
                )}
              </label>
              <div className="rounded-lg border border-outline-variant/30 bg-surface px-3 py-2">
                <p className="mb-1 text-xs text-on-surface-variant">
                  源码路径来自所选工作空间
                </p>
                <p className="break-all font-data text-xs text-on-surface">
                  {repoPath.trim() || "请选择工作空间"}
                </p>
              </div>
              <label className="block">
                <span className="mb-1 block text-xs text-on-surface-variant">
                  执行器覆盖
                </span>
                <select
                  aria-label="执行器覆盖"
                  value={providerOverride}
                  onChange={(event) => setProviderOverride(event.target.value)}
                  className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                >
                  <option value="">使用工作流默认执行器</option>
                  {builderProviderOptions.map((provider) => (
                    <option key={provider.id} value={provider.id}>
                      {provider.label} ({provider.id}:{provider.status})
                    </option>
                  ))}
                </select>
              </label>
              {selectedWorkflowInputs.length > 0 && (
                <div
                  aria-label="Workflow run inputs"
                  className={`rounded-lg border p-3 transition-colors ${
                    workflowInputsUpdated
                      ? "border-primary/35 bg-primary/10"
                      : "border-outline-variant/30 bg-surface"
                  }`}
                >
                  <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                    <p className="text-xs font-medium text-on-surface">
                      工作流输入
                    </p>
                    {workflowInputsUpdated && (
                      <span className="rounded-full bg-primary/12 px-2 py-0.5 text-[10px] font-medium text-primary">
                        已随所选工作流更新
                      </span>
                    )}
                  </div>
                  <div className="space-y-2">
                    {selectedWorkflowInputs.map((input) => {
                      const inputId = String(input.id ?? "");
                      const inputType = String(input.type ?? "text");
                      const required = input.required === true;
                      const role = String(input.role ?? "");
                      const inputName = workflowInputDisplayName(input);
                      const value = inputTextValue(parsedPrepareInputs, input);
                      if (!inputId) return null;
                      if (inputId === "repo_path" && inputType === "directory") {
                        return null;
                      }
                      if (
                        inputId === "repo_path" &&
                        inputType === "directory" &&
                        workspaces.length > 0
                      ) {
                        return (
                          <label key={inputId} className="block">
                            <span className="mb-1 block text-xs text-on-surface-variant">
                              {inputName}
                              {required ? " *" : ""}
                            </span>
                            <select
                              aria-label={`Workflow input ${inputId}`}
                              value={
                                workspaces.some(
                                  (workspace) => workspace.repo_path === value,
                                )
                                  ? value
                                  : ""
                              }
                              onChange={(event) => {
                                const selected = workspaces.find(
                                  (workspace) =>
                                    workspace.repo_path === event.target.value,
                                );
                                if (selected) applyWorkspaceSelection(selected);
                              }}
                              className="w-full rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                            >
                              <option value="" disabled>
                                请选择已创建工作空间
                              </option>
                              {workspaces.map((workspace) => (
                                <option
                                  key={workspace.id}
                                  value={workspace.repo_path}
                                >
                                  {workspace.name} · {workspace.repo_path}
                                </option>
                              ))}
                            </select>
                            <p className="mt-1 break-all font-data text-[10px] text-on-surface-variant">
                              {value || role || "从工作空间选择源码路径"}
                            </p>
                          </label>
                        );
                      }
                      if (inputType === "boolean") {
                        return (
                          <label key={inputId} className="block">
                            <span className="mb-1 block text-xs text-on-surface-variant">
                              {inputName}
                              {required ? " *" : ""}
                            </span>
                            <select
                              aria-label={`Workflow input ${inputId}`}
                              value={value === "true" ? "true" : "false"}
                              onChange={(event) =>
                                updatePrepareInput(input, event.target.value)
                              }
                              className="w-full rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                            >
                              <option value="false">false</option>
                              <option value="true">true</option>
                            </select>
                          </label>
                        );
                      }
                      const multiline =
                        inputType === "file_set" || inputType === "long_text";
                      return (
                        <label key={inputId} className="block">
                          <span className="mb-1 block text-xs text-on-surface-variant">
                            {inputName}
                            {required ? " *" : ""}
                          </span>
                          {multiline ? (
                            <>
                              <textarea
                                aria-label={`Workflow input ${inputId}`}
                                value={value}
                                onChange={(event) =>
                                  updatePrepareInput(input, event.target.value)
                                }
                                placeholder={
                                  inputType === "file_set"
                                    ? "每行一个本地文件路径"
                                    : role || "输入文本"
                                }
                                className="h-20 w-full resize-y rounded-lg border border-outline-variant/30 bg-surface-container p-3 font-data text-xs text-on-surface outline-none focus:border-primary"
                                spellCheck={false}
                              />
                              {inputType === "file_set" && (
                                <input
                                  aria-label={`Upload file for ${inputId}`}
                                  type="file"
                                  multiple
                                  onChange={(event) =>
                                    uploadPrepareInputFile(
                                      input,
                                      event.currentTarget.files,
                                    )
                                  }
                                  className="mt-1 block w-full text-xs text-on-surface-variant file:mr-2 file:rounded file:border-0 file:bg-surface-container-high file:px-2 file:py-1 file:text-xs file:text-on-surface"
                                />
                              )}
                            </>
                          ) : (
                            <>
                              <input
                                aria-label={`Workflow input ${inputId}`}
                                value={value}
                                onChange={(event) =>
                                  updatePrepareInput(input, event.target.value)
                                }
                                placeholder={
                                  isFileLikeWorkflowInput(inputType)
                                    ? "本地文件路径"
                                    : role || "输入值"
                                }
                                className="w-full rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                              />
                              {isFileLikeWorkflowInput(inputType) && (
                                <input
                                  aria-label={`Upload file for ${inputId}`}
                                  type="file"
                                  onChange={(event) =>
                                    uploadPrepareInputFile(
                                      input,
                                      event.currentTarget.files,
                                    )
                                  }
                                  className="mt-1 block w-full text-xs text-on-surface-variant file:mr-2 file:rounded file:border-0 file:bg-surface-container-high file:px-2 file:py-1 file:text-xs file:text-on-surface"
                                />
                              )}
                            </>
                          )}
                        </label>
                      );
                    })}
                  </div>
                </div>
              )}
              <details className="rounded-lg border border-outline-variant/30 bg-surface-container/70 p-3">
                <summary className="cursor-pointer text-xs font-medium text-on-surface">
                  高级输入 JSON
                </summary>
                <label className="mt-2 block">
                  <span className="mb-1 block text-xs text-on-surface-variant">
                    输入 JSON
                  </span>
                  <textarea
                    value={inputsJson}
                    onChange={(event) => setInputsJson(event.target.value)}
                    className="h-40 w-full resize-y rounded-lg border border-outline-variant/30 bg-surface p-3 font-data text-xs text-on-surface outline-none focus:border-primary"
                    aria-label="Inputs JSON"
                    spellCheck={false}
                  />
                </label>
              </details>
              <button
                onClick={createAndRunTaskRun}
                disabled={taskRunActionBusy || !repoPath.trim()}
                className="inline-flex items-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-on-primary transition-opacity hover:opacity-90 disabled:opacity-50"
              >
                {busyAction === "create-and-run-task-run" ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <PlayCircle size={14} />
                )}
                创建并运行
              </button>
              <button
                onClick={prepareTaskRun}
                disabled={taskRunActionBusy || !repoPath.trim()}
                className="ml-2 inline-flex items-center gap-2 rounded-lg bg-surface px-3 py-2 text-sm font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
              >
                {busyAction === "prepare-task-run" ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <PlayCircle size={14} />
                )}
                准备运行
              </button>
              <button
                onClick={executePreparedWorkflow}
                disabled={taskRunActionBusy || !preparedRun}
                className="ml-2 inline-flex items-center gap-2 rounded-lg bg-surface px-3 py-2 text-sm font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
              >
                {busyAction === "execute-workflow" ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <PlayCircle size={14} />
                )}
                执行工作流
              </button>
              <button
                onClick={loadPreparedArtifacts}
                disabled={taskRunActionBusy || !preparedRun}
                className="ml-2 inline-flex items-center gap-2 rounded-lg bg-surface px-3 py-2 text-sm font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
              >
                {busyAction === "load-artifacts" ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <ClipboardList size={14} />
                )}
                审计产物
              </button>
              <button
                onClick={loadTaskRerunPlan}
                disabled={taskRunActionBusy || !preparedRun}
                className="ml-2 inline-flex items-center gap-2 rounded-lg bg-surface px-3 py-2 text-sm font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
              >
                {busyAction === "load-rerun-plan" ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <RefreshCw size={14} />
                )}
                复跑计划
              </button>
              <button
                onClick={generateTaskAcceptanceAudit}
                disabled={taskRunActionBusy || !preparedRun}
                className="ml-2 inline-flex items-center gap-2 rounded-lg bg-surface px-3 py-2 text-sm font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
              >
                {busyAction === "acceptance-audit" ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <Search size={14} />
                )}
                验收审计
              </button>
              <button
                onClick={executeTaskRerunPlan}
                disabled={
                  taskRunActionBusy ||
                  !preparedRun ||
                  !taskRerunPlanValidation?.can_rerun
                }
                className="ml-2 inline-flex items-center gap-2 rounded-lg bg-surface px-3 py-2 text-sm font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
              >
                {busyAction === "execute-rerun-plan" ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <PlayCircle size={14} />
                )}
                执行复跑
              </button>
              <button
                onClick={materializePreparedWorkflowOutputs}
                disabled={
                  taskRunActionBusy || !preparedRun || !workflowExecution
                }
                className="ml-2 inline-flex items-center gap-2 rounded-lg bg-surface px-3 py-2 text-sm font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
              >
                {busyAction === "materialize-workflow-outputs" ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <Database size={14} />
                )}
                固化输出
              </button>
              <button
                onClick={importPreparedSemanticOutputs}
                disabled={
                  taskRunActionBusy ||
                  !preparedRun ||
                  semanticImportOutputIds.length === 0
                }
                className="ml-2 inline-flex items-center gap-2 rounded-lg bg-surface px-3 py-2 text-sm font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
              >
                {busyAction === "import-semantic-outputs" ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <Library size={14} />
                )}
                导入语义
              </button>
              </div>
              <aside
                aria-label="运行结果面板"
                className="ct-run-console min-w-0 rounded-xl border border-outline-variant/30 bg-surface/90 p-3 text-xs shadow-sm"
              >
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <p className="text-sm font-semibold text-on-surface">
                      运行控制台
                    </p>
                    <p className="mt-0.5 text-[11px] text-on-surface-variant">
                      当前节点、失败原因和交付件集中显示
                    </p>
                  </div>
                  <span
                    className={[
                      "rounded-full px-2 py-0.5 text-[11px] font-medium",
                      runPanelStatus === "失败"
                        ? "bg-amber-400/10 text-warning"
                        : runPanelStatus === "已完成"
                          ? "bg-green-500/10 text-green-600"
                          : runPanelStatus === "进行中"
                            ? "bg-primary/10 text-primary"
                            : "bg-surface-container text-on-surface-variant",
                    ].join(" ")}
                  >
                    {runPanelStatus}
                  </span>
                </div>
                <div className="mt-3 flex flex-wrap gap-1 rounded-lg border border-dashed border-outline-variant/40 bg-surface-container/40 p-1">
                  {["空", "进行中", "失败", "已完成"].map((status) => (
                    <span
                      key={status}
                      className={[
                        "rounded-md px-2 py-1 text-[11px] font-medium",
                        runPanelStatus === status
                          ? "bg-surface text-primary shadow-sm"
                          : "text-on-surface-variant",
                      ].join(" ")}
                    >
                      {status === "进行中" ? "运行中" : status}
                    </span>
                  ))}
                </div>
                {!preparedRun ? (
                  <div className="mt-3 rounded-lg border border-dashed border-outline-variant/40 bg-surface-container/40 px-3 py-8 text-center">
                    <PlayCircle
                      size={26}
                      className="mx-auto text-on-surface-variant/60"
                    />
                    <p className="mt-3 text-sm font-semibold text-on-surface">
                      尚无运行记录
                    </p>
                    <p className="mx-auto mt-1 max-w-sm leading-5 text-on-surface-variant">
                      完成左侧输入后启动运行。状态、失败原因和产物会显示在这里。
                    </p>
                  </div>
                ) : (
                  <div className="mt-3 space-y-3">
                    <section
                      className={[
                        "ct-run-state-card rounded-lg border p-3",
                        runPanelStatus === "失败"
                          ? "border-red-300/70 bg-red-50"
                          : runPanelStatus === "已完成"
                            ? "border-emerald-300/70 bg-emerald-50"
                            : "border-sky-300/70 bg-sky-50",
                      ].join(" ")}
                    >
                      <div className="mb-2 flex items-center justify-between gap-2">
                        <div className="min-w-0">
                          <p className="font-semibold text-on-surface">
                            {runPanelStatus === "失败"
                              ? `运行失败 · ${workflowDisplayName(preparedRun.workflow_id)}`
                              : runPanelStatus === "已完成"
                                ? `运行完成 · ${workflowDisplayName(preparedRun.workflow_id)}`
                                : `运行中 · ${workflowDisplayName(preparedRun.workflow_id)}`}
                          </p>
                          <p className="mt-0.5 font-data text-[11px] text-on-surface-variant">
                            Run {preparedRun.task_run_id.slice(0, 8)} · {runPanelProgress.completed}/{runPanelProgress.total} 节点 · {runPanelProgress.percent}%
                          </p>
                        </div>
                        {runPanelStatus === "失败" ? (
                          <AlertTriangle size={22} className="shrink-0 text-red-600" />
                        ) : runPanelStatus === "已完成" ? (
                          <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-emerald-100 text-emerald-700">
                            ✓
                          </span>
                        ) : (
                          <Loader2 size={22} className="shrink-0 animate-spin text-primary" />
                        )}
                      </div>
                      <div className="mb-3 h-1.5 overflow-hidden rounded-full bg-white/70">
                        <div
                          className={[
                            "h-full rounded-full transition-all",
                            runPanelStatus === "失败"
                              ? "bg-red-500"
                              : runPanelStatus === "已完成"
                                ? "bg-emerald-500"
                                : "bg-primary",
                          ].join(" ")}
                          style={{ width: `${runPanelProgress.percent}%` }}
                        />
                      </div>
                      <div className="space-y-1.5">
                        {runPhaseCards.map((phase, index) => {
                          const phaseStatus = runStatusDisplayLabel(phase.status);
                          const isCurrent = index === runPanelProgress.currentIndex;
                          return (
                          <div
                            key={phase.label}
                            className={[
                              "flex min-w-0 items-center gap-2 rounded-md border px-2 py-1.5",
                              phaseStatus === "失败"
                                ? "border-red-200 bg-white text-red-700"
                                : phaseStatus === "已完成"
                                  ? "border-emerald-200 bg-white text-emerald-700"
                                  : isCurrent
                                    ? "border-sky-200 bg-white text-primary"
                                    : "border-outline-variant/20 bg-white/70 text-on-surface-variant",
                            ].join(" ")}
                          >
                            <span className="grid h-4 w-4 shrink-0 place-items-center rounded-full bg-current/10 text-[10px]">
                              {phaseStatus === "已完成"
                                ? "✓"
                                : phaseStatus === "失败"
                                  ? "!"
                                  : isCurrent
                                    ? "•"
                                    : ""}
                            </span>
                            <span className="min-w-0 flex-1">
                              <span className="block truncate font-medium text-on-surface">
                                {phase.label}
                              </span>
                              <span className="block truncate text-[10px] text-on-surface-variant">
                                {phase.detail}
                              </span>
                            </span>
                            <span className="shrink-0 rounded bg-surface-container px-1.5 py-0.5 text-[10px] font-medium">
                              {phaseStatus}
                            </span>
                          </div>
                          );
                        })}
                      </div>
                    </section>
                    {runPanelFailureReasons.length > 0 && (
                      <section className="rounded-lg border border-red-300/70 bg-red-50 p-3">
                        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                          <p className="font-semibold text-red-800">
                            失败原因
                          </p>
                          <div className="flex flex-wrap gap-1.5">
                            <button
                              type="button"
                              onClick={executeTaskRerunPlan}
                              disabled={
                                taskRunActionBusy ||
                                !preparedRun ||
                                !taskRerunPlanValidation?.can_rerun
                              }
                              className="inline-flex items-center gap-1 rounded-md bg-white px-2 py-1 text-[11px] font-medium text-red-700 disabled:opacity-50"
                            >
                              <RefreshCw size={12} />
                              从失败节点重试
                            </button>
                            <button
                              type="button"
                              onClick={() => {
                                setActiveWorkbenchView("workflow");
                              }}
                              className="inline-flex items-center gap-1 rounded-md bg-white px-2 py-1 text-[11px] font-medium text-red-700"
                            >
                              编辑工作流
                            </button>
                          </div>
                        </div>
                        <div className="space-y-1.5">
                          {runPanelFailureReasons.map((reason) => (
                            <div
                              key={reason}
                              className="flex items-start gap-2 rounded-md bg-white px-2 py-1.5 text-red-800"
                            >
                              <AlertTriangle
                                size={13}
                                className="mt-0.5 shrink-0 text-red-600"
                              />
                              <span className="min-w-0 break-words">
                                {reason}
                              </span>
                            </div>
                          ))}
                        </div>
                      </section>
                    )}
                    <section className="rounded-lg border border-outline-variant/25 bg-surface-container/60 p-3">
                      <div className="mb-2 flex items-center justify-between gap-2">
                        <div>
                          <p className="font-semibold text-on-surface">
                            {runPanelStatus === "已完成" ? "交付件" : "运行产物"}
                          </p>
                          <p className="text-[10px] text-on-surface-variant">
                            最终结果优先展示，诊断信息默认折叠
                          </p>
                        </div>
                        <span className="text-[11px] text-on-surface-variant">
                          {artifactManifest?.artifacts.length ?? 0} 个文件
                        </span>
                      </div>
                      <div className="space-y-1.5">
                        {(
                          [
                            ["deliverable", artifactAudienceGroups.deliverable],
                            ["input", artifactAudienceGroups.input],
                            ["support", artifactAudienceGroups.support],
                            [
                              "diagnostic",
                              artifactAudienceGroups.diagnostic,
                            ],
                          ] as const
                        ).map(([audience, artifacts]) => {
                          if (artifacts.length === 0) return null;
                          const label = `${artifactAudienceLabel(audience)} ${artifacts.length}`;
                          const artifactButtons = artifacts
                            .slice(0, 10)
                            .map((artifact) => (
                              <button
                              key={artifact.relative_path}
                              type="button"
                              aria-label={`快速预览 ${artifact.kind}:${artifact.relative_path}${
                                artifact.preview_redacted ? " redacted" : ""
                              }`}
                                onClick={() =>
                                  previewArtifact(artifact.relative_path)
                                }
                                disabled={
                                  taskRunActionBusy ||
                                  busyAction ===
                                    `preview-artifact-${artifact.relative_path}`
                                }
                              className="rounded bg-surface px-2 py-1 text-left font-data text-[10px] text-on-surface-variant transition-colors hover:bg-surface-container-high disabled:opacity-50"
                            >
                              <span className="block max-w-full truncate text-on-surface">
                                {artifactShortName(artifact.relative_path)}
                              </span>
                              <span className="block truncate">
                                {artifact.kind}
                              </span>
                              {artifact.preview_redacted && (
                                <span className="ml-1 text-warning">
                                  redacted
                                  </span>
                                )}
                              </button>
                            ));
                          return (
                            <details
                              key={audience}
                              className="rounded-md border border-outline-variant/20 bg-surface px-2 py-1.5"
                              open={audience === "deliverable"}
                            >
                              <summary className="cursor-pointer select-none font-medium text-on-surface">
                                {label}
                              </summary>
                              <div className="mt-1.5 flex flex-wrap gap-1.5">
                                {artifactButtons}
                              </div>
                            </details>
                          );
                        })}
                        {!artifactManifest?.artifacts.length && (
                          <p className="rounded-md border border-dashed border-outline-variant/30 bg-surface px-2 py-3 text-center text-on-surface-variant">
                            准备运行后展示运行产物
                          </p>
                        )}
                      </div>
                    </section>
                    <details className="rounded-lg border border-outline-variant/25 bg-surface-container/60 p-3">
                      <summary className="cursor-pointer font-semibold text-on-surface">
                        技术诊断
                      </summary>
                      <div className="mt-2 space-y-1 font-data text-[10px] text-on-surface-variant">
                        <p className="break-words">
                          task_run_id:{preparedRun.task_run_id}
                        </p>
                        <p className="break-words">
                          provider:
                          {preparedProviderReadiness?.status ?? "pending"}
                        </p>
                        {preparedProviderReadiness?.warnings
                          .slice(0, 4)
                          .map((warning) => (
                            <p key={warning} className="break-words">
                              warning:{warning}
                            </p>
                          ))}
                      </div>
                    </details>
                  </div>
                )}
              </aside>
              <div className="min-w-0 space-y-3 xl:col-span-2">
              {preparedRun && (
                <details
                  aria-label="运行详细诊断"
                  className="rounded-xl border border-outline-variant/30 bg-surface/80 p-3 text-xs"
                >
                  <summary className="cursor-pointer select-none text-sm font-semibold text-on-surface">
                    查看详细诊断与原始产物
                  </summary>
                  <div className="mt-3 space-y-3">
                <div className="grid gap-3 lg:grid-cols-[1.2fr_1fr]">
                  <div className="rounded-lg border border-outline-variant/30 bg-surface-container/70 p-3">
                    <div className="mb-2 flex items-center justify-between gap-2">
                      <p className="text-xs font-semibold text-on-surface">
                        Agent 运行阶段
                      </p>
                      <span className="font-data text-[10px] text-on-surface-variant">
                        {preparedRun.task_run_id}
                      </span>
                    </div>
                    <div className="grid gap-1.5 sm:grid-cols-4">
                      {runPhaseCards.map((phase) => (
                        <div
                          key={phase.label}
                          className="min-w-0 rounded-md border border-outline-variant/20 bg-surface px-2 py-1.5"
                        >
                          <p className="text-[11px] font-medium text-on-surface">
                            {phase.label}
                          </p>
                          <p className="mt-0.5 font-data text-[10px] text-primary">
                            {phase.status}
                          </p>
                          <p className="mt-0.5 truncate text-[10px] text-on-surface-variant">
                            {phase.detail}
                          </p>
                        </div>
                      ))}
                    </div>
                  </div>
                  <div className="rounded-lg border border-outline-variant/30 bg-surface-container/70 p-3">
                    <p className="mb-2 text-xs font-semibold text-on-surface">
                      可信度与可用性
                    </p>
                    <div className="flex flex-wrap gap-1.5 font-data text-[10px] text-on-surface-variant">
                      <span
                        className={[
                          "rounded bg-surface px-1.5 py-0.5",
                          preparedProviderReadiness?.status === "ready" ||
                          preparedProviderReadiness?.status === "ok"
                            ? "text-on-surface"
                            : "text-warning",
                        ].join(" ")}
                      >
                        provider:
                        {preparedProviderReadiness?.status ?? "pending"}
                      </span>
                      <span className="rounded bg-surface px-1.5 py-0.5">
                        artifacts:{artifactManifest?.artifacts.length ?? 0}
                      </span>
                      <span
                        className={[
                          "rounded bg-surface px-1.5 py-0.5",
                          (taskAcceptanceAudit?.summary.missing_required ?? 0) >
                          0
                            ? "text-warning"
                            : "text-on-surface",
                        ].join(" ")}
                      >
                        缺少必需项:
                        {taskAcceptanceAudit?.summary.missing_required ?? 0}
                      </span>
                      <span className="rounded bg-surface px-1.5 py-0.5">
                        evidence:
                        {workflowExecution?.evidence_materialization
                          ?.evidence_count ??
                          workflowOutputMaterialize?.evidence_count ??
                          0}
                      </span>
                    </div>
                    {preparedProviderReadiness?.warnings.length ? (
                      <p className="mt-1 truncate text-[10px] text-warning">
                        {preparedProviderReadiness.warnings[0]}
                      </p>
                    ) : null}
                  </div>
                  <div className="rounded-lg border border-outline-variant/30 bg-surface-container/70 p-3 lg:col-span-2">
                    <div className="mb-2 flex items-center justify-between gap-2">
                      <p className="text-xs font-semibold text-on-surface">
                        交付物状态
                      </p>
                      <span className="font-data text-[10px] text-on-surface-variant">
                        可预览 / 可下载
                      </span>
                    </div>
                    <div className="grid gap-1.5 sm:grid-cols-2 lg:grid-cols-4">
                      {visibleDeliveryArtifacts.length > 0 ? (
                        visibleDeliveryArtifacts.map((artifact) => (
                          <button
                            key={artifact.relative_path}
                            type="button"
                            onClick={() => previewArtifact(artifact.relative_path)}
                            className="min-w-0 rounded-md border border-outline-variant/20 bg-surface px-2 py-1.5 text-left transition-colors hover:bg-surface-container-high"
                          >
                            <span className="block truncate text-[11px] font-medium text-on-surface">
                              {artifactShortName(artifact.relative_path)}
                            </span>
                            <span className="block truncate font-data text-[10px] text-on-surface-variant">
                              {artifact.kind} · sha:
                              {artifact.sha256.slice(0, 8)}
                            </span>
                          </button>
                        ))
                      ) : (
                        <span className="text-[11px] text-on-surface-variant">
                          准备运行后展示交付物清单
                        </span>
                      )}
                    </div>
                  </div>
                </div>
                <div className="min-w-0 rounded-xl border border-outline-variant/30 bg-surface/80 p-4 text-xs">
                  <p className="font-medium text-on-surface">
                    {preparedRun.task_run_id}
                  </p>
                  <p className="mt-1 break-words font-data text-on-surface-variant">
                    {preparedRun.artifact_dir}
                  </p>
                  <p className="mt-1 text-on-surface-variant">
                    Agent runs: {preparedRun.agent_runs.length}
                  </p>
                  <button
                    type="button"
                    onClick={openPreparedConversation}
                    disabled={openingConversation || taskRunActionBusy}
                    className="mt-3 inline-flex items-center gap-2 rounded-lg bg-primary px-3 py-2 text-xs font-semibold text-on-primary shadow-sm transition-all hover:-translate-y-0.5 hover:shadow-md disabled:translate-y-0 disabled:opacity-50"
                  >
                    {openingConversation ? (
                      <Loader2 size={13} className="animate-spin" />
                    ) : (
                      <MessageSquareText size={13} />
                    )}
                    围绕本次运行继续追问
                  </button>
                  {taskAcceptanceAudit &&
                    taskAcceptanceAudit.task_run_id ===
                      preparedRun.task_run_id && (
                      <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-on-surface-variant">
                        <p>
                          Acceptance:{" "}
                          <span
                            className={
                              taskAcceptanceAudit.status === "ready" ||
                              taskAcceptanceAudit.status === "passed"
                                ? "text-on-surface"
                                : "text-warning"
                            }
                          >
                            {taskAcceptanceAudit.status}
                          </span>
                          <span className="ml-2">
                            artifacts:
                            {taskAcceptanceAudit.summary.artifact_count}
                          </span>
                        </p>
                        <div className="mt-1 flex flex-wrap gap-1.5 font-data text-[10px]">
                          <span className="rounded bg-surface px-1.5 py-0.5">
                            required:
                            {taskAcceptanceAudit.summary.required_checks}
                          </span>
                          <span
                            className={`rounded bg-surface px-1.5 py-0.5 ${
                              taskAcceptanceAudit.summary.missing_required > 0
                                ? "text-warning"
                                : ""
                            }`}
                          >
                            缺少必需项:
                            {taskAcceptanceAudit.summary.missing_required}
                          </span>
                          <span className="rounded bg-surface px-1.5 py-0.5">
                            recommended:
                            {taskAcceptanceAudit.summary.recommended_checks}
                          </span>
                          <span
                            className={`rounded bg-surface px-1.5 py-0.5 ${
                              taskAcceptanceAudit.summary.missing_recommended >
                              0
                                ? "text-warning"
                                : ""
                            }`}
                          >
                            缺少建议项:
                            {taskAcceptanceAudit.summary.missing_recommended}
                          </span>
                        </div>
                        {(() => {
                          const providerIssues =
                            acceptanceProviderIssues(taskAcceptanceAudit);
                          if (providerIssues.length === 0) return null;
                          return (
                            <div className="mt-1 rounded border border-warning/30 bg-surface px-2 py-1.5">
                              <p className="text-[11px] font-medium text-warning">
                                Agent 执行器就绪度
                              </p>
                              <div className="mt-1 space-y-0.5 font-data text-[10px] text-warning">
                                {providerIssues.slice(0, 4).map((issue) => (
                                  <div
                                    key={issue.provider}
                                    className="break-words"
                                  >
                                    {issue.provider}:{runStatusDisplayLabel(issue.status)}
                                    {issue.usedFallback ? " 已使用备用命令" : ""}
                                    {issue.deploymentTaskProbeStatus
                                      ? ` 部署探测:${runStatusDisplayLabel(issue.deploymentTaskProbeStatus)}`
                                      : ""}
                                    {issue.deploymentEvidenceConflict
                                      ? " 部署证据冲突"
                                      : ""}
                                    {issue.deploymentProbeId
                                      ? ` 探测编号:${issue.deploymentProbeId}`
                                      : ""}
                                    {issue.reason
                                      ? ` 原因:${compactReasonLabel(issue.reason)}`
                                      : ""}
                                    {issue.startupProbeEndpoint
                                      ? ` 探测:${issue.startupProbeEndpoint}`
                                      : ""}
                                  </div>
                                ))}
                              </div>
                            </div>
                          );
                        })()}
                        {(() => {
                          const providerIssues =
                            acceptanceCodetalkProviderIssues(
                              taskAcceptanceAudit,
                            );
                          if (providerIssues.length === 0) return null;
                          return (
                            <div className="mt-1 rounded border border-warning/30 bg-surface px-2 py-1.5">
                              <p className="text-[11px] font-medium text-warning">
                                CodeTalk 工具就绪度
                              </p>
                              <div className="mt-1 space-y-0.5 font-data text-[10px] text-warning">
                                {providerIssues.slice(0, 4).map((issue) => (
                                  <div
                                    key={issue.provider}
                                    className="break-words"
                                  >
                                    {issue.provider}:{runStatusDisplayLabel(issue.status)}
                                    {issue.reason
                                      ? ` 原因:${compactReasonLabel(issue.reason)}`
                                      : ""}
                                    {issue.startupProbeEndpoint
                                      ? ` 检查:${issue.startupProbeEndpoint}`
                                      : ""}
                                  </div>
                                ))}
                              </div>
                            </div>
                          );
                        })()}
                        {(() => {
                          const outputIssues =
                            acceptanceWorkflowOutputIssues(taskAcceptanceAudit);
                          if (outputIssues.length === 0) return null;
                          return (
                            <div className="mt-1 rounded border border-warning/30 bg-surface px-2 py-1.5">
                              <p className="text-[11px] font-medium text-warning">
                                工作流输出就绪度
                              </p>
                              <div className="mt-1 space-y-0.5 font-data text-[10px] text-warning">
                                {outputIssues.slice(0, 4).map((issue) => (
                                  <div
                                    key={issue.outputId}
                                    className="break-words"
                                  >
                                    {issue.outputId}:{runStatusDisplayLabel(issue.status)}
                                    {issue.reason
                                      ? ` 原因:${compactReasonLabel(issue.reason)}`
                                      : ""}
                                    {issue.artifact
                                      ? ` 产物:${issue.artifact}`
                                      : ""}
                                    {issue.schemaErrorCount > 0
                                      ? ` Schema 错误:${issue.schemaErrorCount}`
                                      : ""}
                                  </div>
                                ))}
                              </div>
                            </div>
                          );
                        })()}
                        {(() => {
                          const redactionIssues =
                            acceptanceInputRedactionIssues(taskAcceptanceAudit);
                          if (redactionIssues.length === 0) return null;
                          return (
                            <div className="mt-1 rounded border border-warning/30 bg-surface px-2 py-1.5">
                              <p className="text-[11px] font-medium text-warning">
                                Agent 输入脱敏
                              </p>
                              <div className="mt-1 space-y-0.5 font-data text-[10px] text-warning">
                                {redactionIssues.slice(0, 4).map((issue) => (
                                  <div key={issue.id} className="break-words">
                                    {issue.label}
                                    {issue.reason
                                      ? ` 原因:${compactReasonLabel(issue.reason)}`
                                      : ""}
                                    {issue.stdinSha
                                      ? ` stdin-sha:${issue.stdinSha.slice(0, 12)}`
                                      : ""}
                                    {issue.relativePath
                                      ? ` 产物:${issue.relativePath}`
                                      : ""}
                                  </div>
                                ))}
                              </div>
                            </div>
                          );
                        })()}
                        {(() => {
                          const policyIssues =
                            acceptanceInstructionPolicyIssues(
                              taskAcceptanceAudit,
                            );
                          if (policyIssues.length === 0) return null;
                          return (
                            <div className="mt-1 rounded border border-warning/30 bg-surface px-2 py-1.5">
                              <p className="text-[11px] font-medium text-warning">
                                Agent 指令策略
                              </p>
                              <div className="mt-1 space-y-0.5 font-data text-[10px] text-warning">
                                {policyIssues.slice(0, 4).map((issue) => (
                                  <div key={issue.id} className="break-words">
                                    {issue.label}
                                    {issue.reason
                                      ? ` 原因:${compactReasonLabel(issue.reason)}`
                                      : ""}
                                    {issue.expectedFiles.length > 0
                                      ? ` 期望文件:${issue.expectedFiles.slice(0, 3).join(",")}`
                                      : ""}
                                    {issue.relativePath
                                      ? ` 产物:${issue.relativePath}`
                                      : ""}
                                  </div>
                                ))}
                              </div>
                            </div>
                          );
                        })()}
                        {taskAcceptanceAudit.missing_required.length > 0 && (
                          <div className="mt-1 space-y-0.5 font-data text-[10px] text-warning">
                            {taskAcceptanceAudit.missing_required
                              .slice(0, 3)
                              .map((item, index) => (
                                <div
                                  key={`${String(item.id ?? index)}:${index}`}
                                >
                                  缺失项 {index + 1}: {acceptanceIssueLabel(item)}
                                </div>
                              ))}
                          </div>
                        )}
                      </div>
                    )}
                  {taskRerunPlan &&
                    taskRerunPlan.task_run_id === preparedRun.task_run_id && (
                      <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-on-surface-variant">
                        <p>
                          Rerun: {taskRerunPlan.status} / steps{" "}
                          {taskRerunPlan.steps?.length ?? 0}
                        </p>
                        <div className="mt-1 flex flex-wrap gap-1.5 font-data text-[10px]">
                          <span className="rounded bg-surface px-1.5 py-0.5">
                            preserve-inputs:
                            {String(taskRerunPlan.preserve_inputs ?? false)}
                          </span>
                          <span className="rounded bg-surface px-1.5 py-0.5">
                            reuse-bundle:
                            {String(taskRerunPlan.reuse_task_bundle ?? false)}
                          </span>
                          <span className="rounded bg-surface px-1.5 py-0.5">
                            history:{taskRerunHistory?.count ?? 0}
                          </span>
                          {(taskRerunPlan.blocked_outputs?.length ?? 0) > 0 ? (
                            <span className="rounded bg-surface px-1.5 py-0.5 text-warning">
                              blocked:
                              {taskRerunPlan.blocked_outputs?.length ?? 0}
                            </span>
                          ) : null}
                        </div>
                        {taskRerunPlanValidation &&
                          taskRerunPlanValidation.task_run_id ===
                            preparedRun.task_run_id && (
                            <div className="mt-1 flex flex-wrap gap-1.5 font-data text-[10px]">
                              <span
                                className={`rounded bg-surface px-1.5 py-0.5 ${
                                  taskRerunPlanValidation.can_rerun
                                    ? ""
                                    : "text-warning"
                                }`}
                              >
                                validation:{taskRerunPlanValidation.status}
                              </span>
                              <span className="rounded bg-surface px-1.5 py-0.5">
                                can-rerun:
                                {String(taskRerunPlanValidation.can_rerun)}
                              </span>
                              <span className="rounded bg-surface px-1.5 py-0.5">
                                checks:
                                {taskRerunPlanValidation.checks?.length ?? 0}
                              </span>
                              <span className="rounded bg-surface px-1.5 py-0.5">
                                steps:
                                {taskRerunPlanValidation.steps?.length ?? 0}
                              </span>
                            </div>
                          )}
                        {taskRerunExecution && (
                          <div className="mt-1 space-y-0.5 font-data text-[10px] text-on-surface-variant">
                            <p>
                              rerun-execution:{taskRerunExecution.status}{" "}
                              workflow:
                              {taskRerunExecution.execution?.status ??
                                "unknown"}
                            </p>
                            {(() => {
                              const latest = taskRerunHistory?.records?.at(-1);
                              if (!latest) return null;
                              const execution =
                                latest.execution &&
                                typeof latest.execution === "object"
                                  ? (latest.execution as Record<
                                      string,
                                      unknown
                                    >)
                                  : {};
                              const executionArtifactRecord =
                                execution.artifact &&
                                typeof execution.artifact === "object"
                                  ? (execution.artifact as Record<
                                      string,
                                      unknown
                                    >)
                                  : {};
                              const latestArtifactRecord =
                                latest.artifact &&
                                typeof latest.artifact === "object"
                                  ? (latest.artifact as Record<string, unknown>)
                                  : {};
                              const rerunId = String(latest.rerun_id ?? "");
                              const sequence = String(latest.sequence ?? "");
                              const executionArtifact = String(
                                latestArtifactRecord.path ??
                                  latestArtifactRecord.manifest_path ??
                                  executionArtifactRecord.path ??
                                  executionArtifactRecord.manifest_path ??
                                  "task_rerun_execution.json",
                              );
                              return (
                                <div className="rounded bg-surface px-1.5 py-1">
                                  <p>rerun-id:{rerunId || "unknown"}</p>
                                  <p>sequence:{sequence || "unknown"}</p>
                                  <p className="break-words">
                                    history-latest:{executionArtifact}
                                  </p>
                                </div>
                              );
                            })()}
                          </div>
                        )}
                      </div>
                    )}
                  {(() => {
                    const readiness = providerReadinessSummary(
                      preparedRun.task_bundle,
                    );
                    if (!readiness) return null;
                    const visibleCodetalk = readiness.codetalkProviders.filter(
                      (provider) =>
                        ["gitnexus", "cgc", "local-search"].includes(
                          provider.provider,
                        ),
                    );
                    return (
                      <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-on-surface-variant">
                        <p>
                          执行器就绪度:{" "}
                          <span
                            className={
                              readiness.status === "ready"
                                ? "text-on-surface"
                                : "text-warning"
                            }
                          >
                            {readiness.status}
                          </span>
                          <span className="ml-2">
                            repo:{readiness.repoStatus}
                          </span>
                        </p>
                        <div className="mt-1 flex flex-wrap gap-1.5 font-data text-[10px]">
                          {visibleCodetalk.map((provider) => (
                            <span
                              key={provider.provider}
                              className={`rounded bg-surface px-1.5 py-0.5 ${
                                provider.status === "available" ||
                                provider.status === "configured"
                                  ? ""
                                  : "text-warning"
                              }`}
                              title={provider.nextCheck}
                            >
                              {provider.provider}:{provider.status}
                            </span>
                          ))}
                          {readiness.agentProviders.map((provider) => (
                            <span
                              key={provider.provider}
                              className={`rounded bg-surface px-1.5 py-0.5 ${
                                provider.status === "available" &&
                                !provider.deploymentEvidenceConflict
                                  ? ""
                                  : "text-warning"
                              }`}
                              title={[
                                provider.reason,
                                provider.deploymentProbeId
                                  ? `deployment probe:${provider.deploymentProbeId}`
                                  : "",
                              ]
                                .filter(Boolean)
                                .join(" / ")}
                            >
                              {provider.provider}:{provider.status}
                              {provider.deploymentTaskProbeStatus && (
                                <span className="ml-1">
                                  probe:{provider.deploymentTaskProbeStatus}
                                </span>
                              )}
                              {provider.deploymentEvidenceConflict && (
                                <span className="ml-1">conflict</span>
                              )}
                            </span>
                          ))}
                          {readiness.blockingReasons.length > 0 && (
                            <span className="rounded bg-surface px-1.5 py-0.5 text-warning">
                              blocked:{readiness.blockingReasons.join(",")}
                            </span>
                          )}
                          {readiness.warnings.length > 0 && (
                            <span className="rounded bg-surface px-1.5 py-0.5 text-warning">
                              warnings:{readiness.warnings.length}
                            </span>
                          )}
                        </div>
                        {readiness.agentProviders.some(
                          (provider) =>
                            provider.reason ||
                            provider.startupProbeEndpoint ||
                            provider.manualProbeCommand ||
                            provider.configuredCommand,
                        ) && (
                          <div className="mt-1 space-y-0.5 font-data text-[10px]">
                            {readiness.agentProviders
                              .filter(
                                (provider) =>
                                  provider.status !== "available" ||
                                  provider.reason ||
                                  provider.deploymentEvidenceConflict,
                              )
                              .slice(0, 4)
                              .map((provider) => (
                                <div
                                  key={`${provider.provider}:readiness-detail`}
                                  className="break-words"
                                >
                                  {provider.provider}
                                  {provider.configuredCommand
                                    ? ` 命令:${provider.configuredCommand}`
                                    : ""}
                                  {provider.usedFallback ? " 已使用备用命令" : ""}
                                  {provider.reason
                                    ? ` 原因:${compactReasonLabel(provider.reason)}`
                                    : ""}
                                  {provider.startupProbeEndpoint
                                    ? ` 探测:${provider.startupProbeEndpoint}`
                                    : ""}
                                  {provider.manualProbeCommand
                                    ? ` 手动检查:${provider.manualProbeCommand}`
                                    : ""}
                                </div>
                              ))}
                          </div>
                        )}
                      </div>
                    );
                  })()}
                  {(() => {
                    const contextBundle = preparedRun.task_bundle
                      .context_bundle as
                      | {
                          evidence?: unknown[];
                          deployment_evidence?: unknown[];
                          semantic_cases?: unknown[];
                        }
                      | undefined;
                    if (!contextBundle) return null;
                    return (
                      <p className="mt-1 text-on-surface-variant">
                        Context: evidence {contextBundle.evidence?.length ?? 0}{" "}
                        / deployment{" "}
                        {contextBundle.deployment_evidence?.length ?? 0} /
                        semantics {contextBundle.semantic_cases?.length ?? 0}
                      </p>
                    );
                  })()}
                  {(() => {
                    const instructions = preparedRun.task_bundle
                      .agent_instructions as
                      | {
                          files?: unknown[];
                        }
                      | undefined;
                    if (!instructions) return null;
                    return (
                      <p className="mt-1 text-on-surface-variant">
                        Agent instructions: {instructions.files?.length ?? 0}
                      </p>
                    );
                  })()}
                  {(() => {
                    const summary = inputContextSummary(
                      preparedRun.task_bundle,
                    );
                    if (!summary) return null;
                    return (
                      <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-on-surface-variant">
                        <p>Input context: {summary.fileCount} files</p>
                        {summary.inputs.length > 0 && (
                          <div className="mt-1 space-y-1">
                            {summary.inputs.slice(0, 4).map((input, index) => (
                              <div
                                key={`${input.inputId}-${input.filename}-${index}`}
                                className="rounded bg-surface px-1.5 py-1 font-data text-[10px]"
                              >
                                <span className="text-on-surface">
                                  {input.filename || input.inputId}
                                </span>
                                <span className="ml-1">
                                  {input.suffix || input.kind || "file"}
                                </span>
                                <span className="ml-1">
                                  chunks:{input.chunkCount}
                                </span>
                                {input.textTruncated && (
                                  <span className="ml-1 text-warning">
                                    truncated
                                  </span>
                                )}
                                {input.parseWarnings.length > 0 && (
                                  <span className="ml-1 break-words text-warning">
                                    warnings:
                                    {input.parseWarnings.slice(0, 2).join(",")}
                                    {input.parseWarnings.length > 2
                                      ? ",..."
                                      : ""}
                                  </span>
                                )}
                              </div>
                            ))}
                            {summary.inputs.length > 4 && (
                              <p className="font-data text-[10px]">
                                +{summary.inputs.length - 4} more
                              </p>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })()}
                  {(() => {
                    const requests = agentMcpRequestSummary(
                      preparedRun.task_bundle,
                    );
                    if (requests.length === 0) return null;
                    return (
                      <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-on-surface-variant">
                        <p>Agent MCP requests: {requests.length}</p>
                        <div className="mt-1 space-y-1">
                          {requests.slice(0, 4).map((request, index) => (
                            <div
                              key={`${request.inputId}-${index}`}
                              className="rounded bg-surface px-1.5 py-1 font-data text-[10px]"
                            >
                              <span className="text-on-surface">
                                {request.inputId || "mcp_input"}
                              </span>
                              <span className="ml-1">
                                {request.inputType || "input"}
                              </span>
                              <span className="ml-1">
                                owner:{request.credentialOwner || "agent_cli"}
                              </span>
                              <span
                                className={`ml-1 ${
                                  request.codetalkFetchAllowed
                                    ? "text-warning"
                                    : ""
                                }`}
                              >
                                codetalk-fetch:
                                {String(request.codetalkFetchAllowed)}
                              </span>
                              {request.mcpProfiles.length > 0 && (
                                <span className="ml-1">
                                  profiles:{request.mcpProfiles.join(",")}
                                </span>
                              )}
                              {request.requiredArtifacts.length > 0 && (
                                <span className="ml-1 break-words">
                                  artifacts:
                                  {request.requiredArtifacts
                                    .slice(0, 4)
                                    .join(",")}
                                </span>
                              )}
                            </div>
                          ))}
                          {requests.length > 4 && (
                            <p className="font-data text-[10px]">
                              +{requests.length - 4} more
                            </p>
                          )}
                        </div>
                      </div>
                    );
                  })()}
                  {(() => {
                    const summary = fastContextDecisionSummary(
                      preparedRun.task_bundle,
                    );
                    if (!summary) return null;
                    return (
                      <p className="mt-1 font-data text-[11px] text-on-surface-variant">
                        {summary}
                      </p>
                    );
                  })()}
                  {artifactManifest &&
                    artifactManifest.task_run_id ===
                      preparedRun.task_run_id && (
                      <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-on-surface-variant">
                        {(() => {
                          const sortedArtifacts = prioritizedAuditArtifacts(
                            artifactManifest.artifacts,
                          );
                          const groupedArtifacts = {
                            deliverable: sortedArtifacts.filter(
                              (artifact) =>
                                artifactAudience(artifact) === "deliverable",
                            ),
                            input: sortedArtifacts.filter(
                              (artifact) => artifactAudience(artifact) === "input",
                            ),
                            support: sortedArtifacts.filter(
                              (artifact) =>
                                artifactAudience(artifact) === "support",
                            ),
                            diagnostic: sortedArtifacts.filter(
                              (artifact) =>
                                artifactAudience(artifact) === "diagnostic",
                            ),
                          };
                          const primaryArtifacts =
                            groupedArtifacts.deliverable.length > 0
                              ? groupedArtifacts.deliverable
                              : groupedArtifacts.support.slice(0, 6);
                          const supportArtifacts =
                            groupedArtifacts.deliverable.length > 0
                              ? groupedArtifacts.support
                              : groupedArtifacts.support.slice(6);
                          const secondaryGroups = [
                            ["input", groupedArtifacts.input],
                            ["support", supportArtifacts],
                            ["diagnostic", groupedArtifacts.diagnostic],
                          ] as const;
                          const artifactSummary = [
                            `${artifactAudienceLabel("deliverable")}: ${
                              groupedArtifacts.deliverable.length
                            }`,
                            `${artifactAudienceLabel("input")}: ${
                              groupedArtifacts.input.length
                            }`,
                            `${artifactAudienceLabel("diagnostic")}: ${
                              groupedArtifacts.diagnostic.length
                            }`,
                          ].join(" · ");
                          const visibleArtifacts = primaryArtifacts.slice(0, 10);
                          const hiddenArtifacts = primaryArtifacts.slice(
                            visibleArtifacts.length,
                          );
                          const artifactButton = (
                            artifact: WorkbenchTaskArtifact,
                          ) => (
                            <button
                              key={artifact.relative_path}
                              onClick={() =>
                                previewArtifact(artifact.relative_path)
                              }
                              disabled={
                                taskRunActionBusy ||
                                busyAction ===
                                  `preview-artifact-${artifact.relative_path}`
                              }
                              className="rounded bg-surface px-1.5 py-0.5 text-left font-data text-[10px] transition-colors hover:bg-surface-container-high disabled:opacity-50"
                            >
                              {artifact.kind}:{artifact.relative_path}
                              {artifact.preview_redacted && (
                                <span className="ml-1 text-warning">
                                  redacted
                                </span>
                              )}
                            </button>
                          );
                          return (
                            <>
                              <div className="flex flex-wrap items-center gap-2">
                                <span className="font-medium text-on-surface">
                                  运行产物
                                </span>
                                <span className="font-data text-[10px]">
                                  {artifactSummary}
                                </span>
                              </div>
                              <div className="mt-1 flex flex-wrap gap-1.5">
                                {visibleArtifacts.map(artifactButton)}
                              </div>
                              {hiddenArtifacts.length > 0 && (
                                <details className="mt-1 rounded bg-surface/70 px-2 py-1">
                                  <summary className="cursor-pointer text-[11px] font-medium text-on-surface">
                                    展开其余 {hiddenArtifacts.length} 个交付文件
                                  </summary>
                                  <div className="mt-1 flex flex-wrap gap-1.5">
                                    {hiddenArtifacts.map(artifactButton)}
                                  </div>
                                </details>
                              )}
                              {secondaryGroups.map(([audience, artifacts]) =>
                                artifacts.length > 0 ? (
                                  <details
                                    key={audience}
                                    className="mt-1 rounded bg-surface/70 px-2 py-1"
                                  >
                                    <summary className="cursor-pointer text-[11px] font-medium text-on-surface">
                                      {artifactAudienceLabel(audience)}{" "}
                                      {artifacts.length}
                                    </summary>
                                    <div className="mt-1 flex flex-wrap gap-1.5">
                                      {artifacts.map(artifactButton)}
                                    </div>
                                  </details>
                                ) : null,
                              )}
                            </>
                          );
                        })()}
                        {artifactContent && (
                          <div className="mt-2 rounded border border-outline-variant/30 bg-surface p-2">
                            <div className="flex flex-wrap items-center gap-2 text-[11px]">
                              <span className="font-medium text-on-surface">
                                {artifactContent.relative_path}
                              </span>
                              <span className="font-data">
                                {artifactContent.kind}
                              </span>
                              <span className="font-data">
                                sha:{artifactContent.sha256.slice(0, 12)}
                              </span>
                              {artifactContent.truncated && (
                                <span className="text-warning">truncated</span>
                              )}
                              {artifactContent.content_redacted && (
                                <span className="text-warning">redacted</span>
                              )}
                              {artifactContent.is_text && (
                                <button
                                  type="button"
                                  title={
                                    artifactContent.content_redacted
                                      ? "下载当前脱敏后的预览内容"
                                      : "下载当前预览内容"
                                  }
                                  onClick={() =>
                                    downloadTextFile(
                                      safeArtifactDownloadFilename(
                                        artifactContent.relative_path,
                                      ),
                                      artifactContent.content,
                                      "text/plain;charset=utf-8",
                                    )
                                  }
                                  className="inline-flex items-center gap-1 rounded bg-surface-container px-1.5 py-0.5 font-medium text-on-surface transition-colors hover:bg-surface-container-high"
                                >
                                  <Download size={12} />
                                  {artifactContent.content_redacted
                                    ? "下载脱敏预览"
                                    : "下载预览"}
                                </button>
                              )}
                            </div>
                            {(() => {
                              const summary =
                                evidenceValidationSummary(artifactContent);
                              if (!summary) return null;
                              return (
                                <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-[11px] text-on-surface-variant">
                                  <div className="flex flex-wrap gap-2">
                                    <span>
                                      Accepted artifacts:{" "}
                                      {summary.acceptedCount}
                                    </span>
                                    <span>
                                      Rejected artifacts:{" "}
                                      {summary.rejectedCount}
                                    </span>
                                  </div>
                                  {summary.acceptedDetails.length > 0 && (
                                    <div className="mt-1 space-y-0.5 font-data text-[10px]">
                                      {summary.acceptedDetails
                                        .slice(0, 4)
                                        .map((item) => (
                                          <div
                                            key={`${item.sourceStepId}:${item.artifact}`}
                                          >
                                            {item.artifact} sha:
                                            {item.sha256.slice(0, 12)}
                                          </div>
                                        ))}
                                    </div>
                                  )}
                                  {summary.rejectedDetails.length > 0 && (
                                    <div className="mt-1 space-y-0.5 font-data text-[10px] text-warning">
                                      {summary.rejectedDetails
                                        .slice(0, 3)
                                        .map((item) => (
                                          <div
                                            key={`${item.sourceStepId}:${item.artifact}:${item.reason}`}
                                          >
                                            {item.artifact || "artifact"}{" "}
                                            rejected:{item.reason || "unknown"}
                                          </div>
                                        ))}
                                    </div>
                                  )}
                                </div>
                              );
                            })()}
                            {(() => {
                              const summary =
                                workflowOutputMaterializationSummary(
                                  artifactContent,
                                );
                              if (!summary) return null;
                              return (
                                <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-[11px] text-on-surface-variant">
                                  <div className="flex flex-wrap gap-2">
                                    <span>
                                      Materialized evidence:{" "}
                                      {summary.evidenceCount}
                                    </span>
                                    <span>
                                      Rejected outputs: {summary.rejectedCount}
                                    </span>
                                    <span>
                                      Declared outputs: {summary.outputCount}
                                    </span>
                                    {summary.auditSummary
                                      .evidenceMemoryDeclaredCount > 0 && (
                                      <span>
                                        evidence memory:
                                        {
                                          summary.auditSummary
                                            .evidenceMemoryDeclaredCount
                                        }
                                      </span>
                                    )}
                                  </div>
                                  {summary.auditOutputs.length > 0 && (
                                    <div className="mt-1 space-y-1 font-data text-[10px]">
                                      {summary.auditOutputs
                                        .slice(0, 4)
                                        .map((item) => (
                                          <div
                                            key={item.outputId}
                                            className={
                                              item.materializationStatus ===
                                              "accepted"
                                                ? "text-on-surface"
                                                : item.materializationStatus ===
                                                    "partial"
                                                  ? "text-warning"
                                                  : "text-on-surface-variant"
                                            }
                                          >
                                            {item.outputId}:
                                            {item.materializationStatus ||
                                              "unknown"}
                                            {item.artifact
                                              ? ` artifact:${item.artifact}`
                                              : ""}
                                            {item.mappingKind
                                              ? ` mapping:${item.mappingKind}`
                                              : ""}
                                            {item.materializedCount
                                              ? ` evidence:${item.materializedCount}`
                                              : ""}
                                            {item.rejectedCount
                                              ? ` rejected:${item.rejectedCount}`
                                              : ""}
                                            {item.rejectionReasons.length > 0
                                              ? ` reason:${item.rejectionReasons[0]}`
                                              : ""}
                                          </div>
                                        ))}
                                    </div>
                                  )}
                                  {summary.firstRejected && (
                                    <div className="mt-1 flex flex-wrap gap-2">
                                      <span>
                                        First rejected:{" "}
                                        {summary.firstRejected.output}
                                      </span>
                                      <span>
                                        reason:{summary.firstRejected.reason}
                                      </span>
                                      {summary.firstRejected.status && (
                                        <span>
                                          status:{summary.firstRejected.status}
                                        </span>
                                      )}
                                      {summary.firstRejected.schemaErrorCount >
                                        0 && (
                                        <span>
                                          schema errors:
                                          {
                                            summary.firstRejected
                                              .schemaErrorCount
                                          }
                                        </span>
                                      )}
                                    </div>
                                  )}
                                  {summary.workflowOutputsSha && (
                                    <div className="mt-1 font-data text-[10px]">
                                      workflow_outputs sha:
                                      {summary.workflowOutputsSha.slice(0, 12)}
                                    </div>
                                  )}
                                  {summary.materializedEvidence.length > 0 && (
                                    <div className="mt-1 space-y-0.5 font-data text-[10px]">
                                      {summary.materializedEvidence
                                        .slice(0, 4)
                                        .map((item) => (
                                          <div
                                            key={`${item.evidenceId}:${item.kind}`}
                                          >
                                            {item.kind}:
                                            {item.subjectKey || item.evidenceId}
                                            {item.outputId
                                              ? ` output:${item.outputId}`
                                              : ""}
                                            {item.mappingKind
                                              ? ` mapping:${item.mappingKind}`
                                              : ""}
                                            {item.sourceStepId
                                              ? ` step:${item.sourceStepId}`
                                              : ""}
                                          </div>
                                        ))}
                                    </div>
                                  )}
                                </div>
                              );
                            })()}
                            {(() => {
                              const summary =
                                blackBoxGenerationPolicySummary(
                                  artifactContent,
                                );
                              if (!summary) return null;
                              return (
                                <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-[11px] text-on-surface-variant">
                                  <div className="flex flex-wrap gap-2">
                                    <span>
                                      Black-box terms: {summary.termCount}
                                    </span>
                                    <span>cases:{summary.caseCount}</span>
                                    {summary.firstCaseId && (
                                      <span>{summary.firstCaseId}</span>
                                    )}
                                  </div>
                                  <div className="mt-1 flex flex-wrap gap-2 font-data text-[10px]">
                                    {summary.firstTerms
                                      .slice(0, 4)
                                      .map((term) => (
                                        <span key={term}>term:{term}</span>
                                      ))}
                                  </div>
                                  <div className="mt-1 flex flex-wrap gap-2 font-data text-[10px]">
                                    {summary.allowedUses
                                      .slice(0, 3)
                                      .map((use) => (
                                        <span key={use}>allowed:{use}</span>
                                      ))}
                                  </div>
                                  <div className="mt-1 flex flex-wrap gap-2 font-data text-[10px] text-warning">
                                    {summary.mustNotUse
                                      .slice(0, 3)
                                      .map((use) => (
                                        <span key={use}>must-not:{use}</span>
                                      ))}
                                  </div>
                                  {summary.authorityRule && (
                                    <div className="mt-1 break-words text-[10px]">
                                      {summary.authorityRule}
                                    </div>
                                  )}
                                </div>
                              );
                            })()}
                            {(() => {
                              const summary =
                                memoryArtifactSummary(artifactContent);
                              if (!summary) return null;
                              return (
                                <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-[11px] text-on-surface-variant">
                                  <div className="flex flex-wrap gap-2">
                                    <span>
                                      {summary.kind === "memory_retrieval"
                                        ? "Memory retrieval"
                                        : "Context bundle"}
                                    </span>
                                    <span>
                                      evidence:{summary.evidenceCount}
                                    </span>
                                    <span>
                                      deployment:{summary.deploymentCount}
                                    </span>
                                    <span>
                                      semantics:{summary.semanticCount}
                                    </span>
                                    <span>
                                      slices:{summary.sourceSliceCount}
                                    </span>
                                  </div>
                                  {summary.query && (
                                    <div className="mt-1 break-words font-data text-[10px]">
                                      query:{summary.query}
                                    </div>
                                  )}
                                  <div className="mt-1 flex flex-wrap gap-2 font-data text-[10px]">
                                    {summary.firstSubject && (
                                      <span>first:{summary.firstSubject}</span>
                                    )}
                                    {summary.firstDeploymentSubject && (
                                      <span>
                                        deployment:
                                        {summary.firstDeploymentSubject}
                                      </span>
                                    )}
                                  </div>
                                  {summary.firstReuseReason && (
                                    <div className="mt-1 break-words text-[10px]">
                                      reuse:{summary.firstReuseReason}
                                    </div>
                                  )}
                                </div>
                              );
                            })()}
                            {(() => {
                              const summary =
                                inputMaterialsSummary(artifactContent);
                              if (!summary) return null;
                              return (
                                <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-[11px] text-on-surface-variant">
                                  <div className="flex flex-wrap gap-2">
                                    <span>Input materials</span>
                                    <span>
                                      materials:{summary.materialCount}
                                    </span>
                                    <span>
                                      must-read:{String(summary.mustRead)}
                                    </span>
                                    <span>
                                      source-truth:
                                      {String(summary.materialsAreSourceTruth)}
                                    </span>
                                  </div>
                                  {summary.readOrder.length > 0 && (
                                    <div className="mt-1 break-words font-data text-[10px]">
                                      read-order:
                                      {summary.readOrder.slice(0, 6).join(",")}
                                    </div>
                                  )}
                                  <div className="mt-1 flex flex-wrap gap-2 font-data text-[10px]">
                                    {summary.firstInputId && (
                                      <span>first:{summary.firstInputId}</span>
                                    )}
                                    {summary.firstRole && (
                                      <span>role:{summary.firstRole}</span>
                                    )}
                                    {summary.firstFilename && (
                                      <span>file:{summary.firstFilename}</span>
                                    )}
                                    {summary.firstSha && (
                                      <span>
                                        sha:{summary.firstSha.slice(0, 12)}
                                      </span>
                                    )}
                                  </div>
                                  {summary.firstChunksPath && (
                                    <div className="mt-1 break-words font-data text-[10px]">
                                      chunks:{summary.firstChunksPath}
                                    </div>
                                  )}
                                </div>
                              );
                            })()}
                            {(() => {
                              const summary =
                                failureRetryContextSummary(artifactContent);
                              if (!summary) return null;
                              return (
                                <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-[11px] text-on-surface-variant">
                                  <div className="flex flex-wrap gap-2">
                                    <span>Failure retry</span>
                                    {summary.stepId && (
                                      <span>step:{summary.stepId}</span>
                                    )}
                                    {summary.failureKind && (
                                      <span>kind:{summary.failureKind}</span>
                                    )}
                                    <span>
                                      retryable:{String(summary.retryable)}
                                    </span>
                                    {summary.exitCode && (
                                      <span>exit:{summary.exitCode}</span>
                                    )}
                                  </div>
                                  {summary.missingArtifacts.length > 0 && (
                                    <div className="mt-1 break-words font-data text-[10px]">
                                      missing:
                                      {summary.missingArtifacts
                                        .slice(0, 6)
                                        .join(",")}
                                    </div>
                                  )}
                                  {summary.mustProduceArtifacts.length > 0 && (
                                    <div className="mt-1 break-words font-data text-[10px]">
                                      must-produce:
                                      {summary.mustProduceArtifacts
                                        .slice(0, 6)
                                        .join(",")}
                                    </div>
                                  )}
                                  {summary.doNotRepeat.length > 0 && (
                                    <div className="mt-1 flex flex-wrap gap-2 font-data text-[10px] text-warning">
                                      {summary.doNotRepeat
                                        .slice(0, 3)
                                        .map((item) => (
                                          <span key={item}>do-not:{item}</span>
                                        ))}
                                    </div>
                                  )}
                                  {summary.stderrExcerpt && (
                                    <div className="mt-1 break-words text-[10px]">
                                      stderr:
                                      {summary.stderrExcerpt.slice(0, 180)}
                                    </div>
                                  )}
                                  {summary.stdoutExcerpt && (
                                    <div className="mt-1 break-words text-[10px]">
                                      stdout:
                                      {summary.stdoutExcerpt.slice(0, 180)}
                                    </div>
                                  )}
                                </div>
                              );
                            })()}
                            {(() => {
                              const summary =
                                replayPlanSummary(artifactContent);
                              if (!summary) return null;
                              return (
                                <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-[11px] text-on-surface-variant">
                                  <div className="flex flex-wrap gap-2">
                                    <span>
                                      Replay status: {summary.replayStatus}
                                    </span>
                                    {summary.provider && (
                                      <span>provider:{summary.provider}</span>
                                    )}
                                    {summary.turnId && (
                                      <span>turn:{summary.turnId}</span>
                                    )}
                                    {summary.promptSource && (
                                      <span>prompt:{summary.promptSource}</span>
                                    )}
                                    {summary.promptTransport && (
                                      <span>
                                        transport:{summary.promptTransport}
                                      </span>
                                    )}
                                    {summary.timeoutSec > 0 && (
                                      <span>timeout:{summary.timeoutSec}s</span>
                                    )}
                                    <span>
                                      readonly:
                                      {String(summary.readonlyRequired)}
                                    </span>
                                    <span>
                                      validates:
                                      {String(summary.validatesOutputs)}
                                    </span>
                                    <span>hashes:{summary.hashCount}</span>
                                  </div>
                                  <div className="mt-1 flex flex-wrap gap-2 font-data text-[10px]">
                                    {summary.taskBundleSha && (
                                      <span>
                                        task_bundle sha:
                                        {summary.taskBundleSha.slice(0, 12)}
                                      </span>
                                    )}
                                    {summary.executionInputSha && (
                                      <span>
                                        execution_input sha:
                                        {summary.executionInputSha.slice(0, 12)}
                                      </span>
                                    )}
                                    {summary.contractSha && (
                                      <span>
                                        contract sha:
                                        {summary.contractSha.slice(0, 12)}
                                      </span>
                                    )}
                                  </div>
                                  {summary.cwd && (
                                    <div className="mt-1 break-words font-data text-[10px]">
                                      cwd:{summary.cwd}
                                    </div>
                                  )}
                                </div>
                              );
                            })()}
                            {(() => {
                              const summary =
                                executionInputSummary(artifactContent);
                              if (!summary) return null;
                              return (
                                <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-[11px] text-on-surface-variant">
                                  <div className="flex flex-wrap gap-2">
                                    <span>Execution input</span>
                                    {summary.provider && (
                                      <span>provider:{summary.provider}</span>
                                    )}
                                    {summary.turnId && (
                                      <span>turn:{summary.turnId}</span>
                                    )}
                                    {summary.promptTransport && (
                                      <span>
                                        transport:{summary.promptTransport}
                                      </span>
                                    )}
                                    {summary.promptTransportReason && (
                                      <span>
                                        reason:{summary.promptTransportReason}
                                      </span>
                                    )}
                                    {summary.timeoutSec > 0 && (
                                      <span>timeout:{summary.timeoutSec}s</span>
                                    )}
                                    <span>
                                      stdin redacted:
                                      {String(summary.stdinRedacted)}
                                    </span>
                                    {summary.readonlyEnv && (
                                      <span>
                                        readonly env:{summary.readonlyEnv}
                                      </span>
                                    )}
                                  </div>
                                  <div className="mt-1 flex flex-wrap gap-2 font-data text-[10px]">
                                    {summary.stdinSha && (
                                      <span>
                                        stdin sha:
                                        {summary.stdinSha.slice(0, 12)}
                                      </span>
                                    )}
                                    {summary.outputContractSha && (
                                      <span>
                                        contract sha:
                                        {summary.outputContractSha.slice(0, 12)}
                                      </span>
                                    )}
                                  </div>
                                  {summary.cwd && (
                                    <div className="mt-1 break-words font-data text-[10px]">
                                      cwd:{summary.cwd}
                                    </div>
                                  )}
                                </div>
                              );
                            })()}
                            {artifactContent.content_redacted ? (
                              <p className="mt-2 rounded bg-surface-container p-2 text-[11px] text-warning">
                                Artifact content is redacted and hidden from
                                inline preview.
                              </p>
                            ) : artifactContent.is_text ? (
                              <pre className="mt-2 max-h-52 overflow-auto whitespace-pre-wrap break-words rounded bg-surface-container p-2 font-data text-[10px] text-on-surface">
                                {artifactContent.content}
                              </pre>
                            ) : (
                              <p className="mt-2 text-[11px] text-on-surface-variant">
                                Binary artifact content is not rendered inline.
                              </p>
                            )}
                          </div>
                        )}
                      </div>
                    )}
                  {workflowExecution && (
                    <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-on-surface-variant">
                      工作流: {workflowExecution.status} / steps{" "}
                      {workflowExecution.step_results.length} / outputs{" "}
                      {workflowExecution.outputs?.length ?? 0}
                      {workflowExecution.audit_summary && (
                        <div className="mt-1 flex flex-wrap gap-1.5 font-data text-[10px]">
                          <span className="rounded bg-surface px-1.5 py-0.5">
                            agent:
                            {workflowExecution.audit_summary.agent_step_count ??
                              0}
                          </span>
                          <span className="rounded bg-surface px-1.5 py-0.5">
                            invalid:
                            {workflowExecution.audit_summary.invalid_steps ?? 0}
                          </span>
                          <span className="rounded bg-surface px-1.5 py-0.5">
                            errors:
                            {workflowExecution.audit_summary.error_steps ?? 0}
                          </span>
                          <span className="rounded bg-surface px-1.5 py-0.5">
                            lifecycle:
                            {workflowExecution.audit_summary
                              .agent_lifecycle_artifacts?.length ?? 0}
                          </span>
                          {workflowExecution.audit_summary.failure_kinds
                            ?.length ? (
                            <span className="rounded bg-surface px-1.5 py-0.5 text-warning">
                              failure:
                              {workflowExecution.audit_summary.failure_kinds.join(
                                ",",
                              )}
                            </span>
                          ) : null}
                          {workflowExecution.audit_summary.missing_artifacts
                            ?.length ? (
                            <span className="rounded bg-surface px-1.5 py-0.5 text-warning">
                              missing:
                              {workflowExecution.audit_summary.missing_artifacts.join(
                                ",",
                              )}
                            </span>
                          ) : null}
                        </div>
                      )}
                      {workflowExecution.rerun_plan && (
                        <div className="mt-1 flex flex-wrap gap-1.5 font-data text-[10px]">
                          <span className="rounded bg-surface px-1.5 py-0.5">
                            rerun:
                            {workflowExecution.rerun_plan.status ?? "unknown"}
                          </span>
                          <span className="rounded bg-surface px-1.5 py-0.5">
                            rerun-steps:
                            {workflowExecution.rerun_plan.steps?.length ?? 0}
                          </span>
                          {(workflowExecution.rerun_plan.blocked_outputs
                            ?.length ?? 0) > 0 ? (
                            <span className="rounded bg-surface px-1.5 py-0.5 text-warning">
                              blocked-outputs:
                              {workflowExecution.rerun_plan.blocked_outputs
                                ?.length ?? 0}
                            </span>
                          ) : null}
                        </div>
                      )}
                      {workflowExecution.evidence_materialization && (
                        <div className="mt-1 flex flex-wrap gap-1.5 font-data text-[10px]">
                          <span
                            className={`rounded bg-surface px-1.5 py-0.5 ${
                              workflowExecution.evidence_materialization
                                .status === "ok"
                                ? ""
                                : "text-warning"
                            }`}
                          >
                            evidence:
                            {workflowExecution.evidence_materialization.status}
                          </span>
                          <span className="rounded bg-surface px-1.5 py-0.5">
                            evidence-items:
                            {
                              workflowExecution.evidence_materialization
                                .evidence_count
                            }
                          </span>
                          {workflowExecution.evidence_materialization
                            .rejected_outputs.length > 0 ? (
                            <span className="rounded bg-surface px-1.5 py-0.5 text-warning">
                              rejected:
                              {
                                workflowExecution.evidence_materialization
                                  .rejected_outputs.length
                              }
                            </span>
                          ) : null}
                        </div>
                      )}
                      {workflowExecution.semantic_output_import && (
                        <div className="mt-1 flex flex-wrap gap-1.5 font-data text-[10px]">
                          <span
                            className={`rounded bg-surface px-1.5 py-0.5 ${
                              workflowExecution.semantic_output_import
                                .status === "ok" ||
                              workflowExecution.semantic_output_import
                                .status === "skipped"
                                ? ""
                                : "text-warning"
                            }`}
                          >
                            semantics:
                            {workflowExecution.semantic_output_import.status ??
                              "unknown"}
                          </span>
                          <span className="rounded bg-surface px-1.5 py-0.5">
                            semantic-cases:
                            {
                              workflowExecution.semantic_output_import
                                .imported_count
                            }
                          </span>
                          {workflowExecution.semantic_output_import
                            .rejected_count > 0 ? (
                            <span className="rounded bg-surface px-1.5 py-0.5 text-warning">
                              rejected:
                              {
                                workflowExecution.semantic_output_import
                                  .rejected_count
                              }
                            </span>
                          ) : null}
                        </div>
                      )}
                      {(workflowExecution.outputs?.length ?? 0) > 0 && (
                        <div className="mt-1 flex flex-wrap gap-1.5">
                          {workflowExecution.outputs?.map((output, index) => (
                            <span
                              key={`${String(output.id ?? "output")}-${index}`}
                              className="rounded bg-surface px-1.5 py-0.5 font-data text-[10px]"
                            >
                              {String(output.id ?? "output")}:
                              {String(output.status ?? "unknown")}
                            </span>
                          ))}
                        </div>
                      )}
                      {workflowExecution.step_results.length > 0 && (
                        <div className="mt-1 space-y-1">
                          {workflowExecution.step_results.map((step, index) => {
                            const diagnostics = step.provider_diagnostics;
                            const recovery = step.failure_recovery;
                            const recoveryDiagnostics =
                              recovery?.provider_diagnostics;
                            const displayedDiagnostics =
                              diagnostics ?? recoveryDiagnostics;
                            const firstAttempt =
                              recoveryDiagnostics?.attempts?.[0];
                            if (!displayedDiagnostics && !recovery) return null;
                            return (
                              <div
                                key={`${String(step.step_id ?? "step")}-${index}`}
                                className="rounded bg-surface px-1.5 py-1 font-data text-[10px]"
                              >
                                {displayedDiagnostics && (
                                  <>
                                    <span className="text-on-surface">
                                      {String(step.step_id ?? "step")} provider:
                                      {displayedDiagnostics.provider ||
                                        String(step.provider ?? "")}
                                    </span>
                                    <span className="ml-1">
                                      health:
                                      {displayedDiagnostics.health_status ||
                                        "unknown"}
                                    </span>
                                  </>
                                )}
                                {!displayedDiagnostics && (
                                  <span className="text-on-surface">
                                    {String(step.step_id ?? "step")}
                                  </span>
                                )}
                                {displayedDiagnostics?.prompt_transport && (
                                  <span className="ml-1">
                                    transport:
                                    {displayedDiagnostics.prompt_transport}
                                  </span>
                                )}
                                {displayedDiagnostics?.command_resolution_source && (
                                  <span className="ml-1">
                                    command:
                                    {
                                      displayedDiagnostics.command_resolution_source
                                    }
                                  </span>
                                )}
                                {displayedDiagnostics?.command_resolution_used_fallback && (
                                  <span className="ml-1 text-warning">
                                    fallback
                                  </span>
                                )}
                                {displayedDiagnostics?.command_resolution_reason && (
                                  <span className="ml-1">
                                    reason:
                                    {
                                      displayedDiagnostics.command_resolution_reason
                                    }
                                  </span>
                                )}
                                {displayedDiagnostics?.startup_probe_endpoint && (
                                  <span className="ml-1 break-all">
                                    probe:
                                    {
                                      displayedDiagnostics.startup_probe_endpoint
                                    }
                                  </span>
                                )}
                                {recovery && (
                                  <div className="mt-1 text-warning">
                                    <span>
                                      recovery:
                                      {recovery.failure_kind || "unknown"}
                                    </span>
                                    {recovery.validation_status && (
                                      <span className="ml-1">
                                        validation:{recovery.validation_status}
                                      </span>
                                    )}
                                    {recovery.missing_artifacts?.length ? (
                                      <span className="ml-1">
                                        missing:
                                        {recovery.missing_artifacts.join(",")}
                                      </span>
                                    ) : null}
                                    {recovery.suggested_actions?.[0] && (
                                      <span className="ml-1">
                                        next:{recovery.suggested_actions[0]}
                                      </span>
                                    )}
                                    {recoveryDiagnostics?.configured_command_text && (
                                      <span className="ml-1 break-all">
                                        configured:
                                        {
                                          recoveryDiagnostics.configured_command_text
                                        }
                                      </span>
                                    )}
                                    {firstAttempt && (
                                      <span className="ml-1 break-all">
                                        attempt:
                                        {firstAttempt.command ||
                                          firstAttempt.executable ||
                                          "agent"}
                                        ={firstAttempt.status || "unknown"}
                                        {firstAttempt.reason
                                          ? `:${firstAttempt.reason}`
                                          : ""}
                                      </span>
                                    )}
                                  </div>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  )}
                  {workflowOutputMaterialize && (
                    <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-on-surface-variant">
                      <p>
                        Output evidence: {workflowOutputMaterialize.status} /{" "}
                        {workflowOutputMaterialize.evidence_count} items
                        {workflowOutputMaterialize.rejected_outputs.length >
                          0 && (
                          <span className="ml-2 text-warning">
                            rejected{" "}
                            {workflowOutputMaterialize.rejected_outputs.length}
                          </span>
                        )}
                      </p>
                      {workflowOutputMaterialize.rejected_outputs.length >
                        0 && (
                        <div className="mt-1 space-y-0.5 font-data text-[10px] text-warning">
                          {workflowOutputMaterialize.rejected_outputs
                            .slice(0, 4)
                            .map((item, index) => (
                              <div
                                key={`${rejectedOutputLabel(item)}:${index}`}
                                className="break-words"
                              >
                                {rejectedOutputLabel(item)} rejected:
                                {rejectedOutputReason(item)}
                              </div>
                            ))}
                          {workflowOutputMaterialize.rejected_outputs.length >
                            4 && (
                            <div>
                              +
                              {workflowOutputMaterialize.rejected_outputs
                                .length - 4}{" "}
                              more
                            </div>
                          )}
                        </div>
                      )}
                      {(() => {
                        const outputs = materializationAuditOutputs(
                          workflowOutputMaterialize,
                        );
                        if (outputs.length === 0) return null;
                        return (
                          <div className="mt-1 space-y-0.5 font-data text-[10px]">
                            {outputs.slice(0, 4).map((item) => (
                              <div
                                key={item.outputId}
                                className={
                                  item.materializationStatus === "accepted"
                                    ? "text-on-surface"
                                    : item.materializationStatus === "partial"
                                      ? "text-warning"
                                      : "text-on-surface-variant"
                                }
                              >
                                {item.outputId}:
                                {item.materializationStatus || "unknown"}
                                {item.artifact
                                  ? ` artifact:${item.artifact}`
                                  : ""}
                                {item.materializedCount
                                  ? ` evidence:${item.materializedCount}`
                                  : ""}
                                {item.rejectedCount
                                  ? ` rejected:${item.rejectedCount}`
                                  : ""}
                              </div>
                            ))}
                          </div>
                        );
                      })()}
                    </div>
                  )}
                  {semanticOutputImport && (
                    <div className="mt-2 rounded bg-surface-container px-2 py-1.5 text-on-surface-variant">
                      <p>
                        Semantic import:{" "}
                        {semanticOutputImport.status ?? "unknown"} /{" "}
                        {semanticOutputImport.imported_count} imported
                        {semanticOutputImport.rejected_count > 0 && (
                          <span className="ml-2 text-warning">
                            rejected {semanticOutputImport.rejected_count}
                          </span>
                        )}
                      </p>
                      {semanticOutputImport.source_ref && (
                        <p className="mt-1 break-words font-data text-[10px]">
                          source:{semanticOutputImport.source_ref}
                        </p>
                      )}
                      {semanticOutputImport.rejected.length > 0 && (
                        <div className="mt-1 space-y-0.5 font-data text-[10px] text-warning">
                          {semanticOutputImport.rejected
                            .slice(0, 4)
                            .map((item, index) => (
                              <div
                                key={`${String(item.output ?? item.case_id ?? "case")}:${index}`}
                                className="break-words"
                              >
                                {String(item.output ?? item.case_id ?? "case")}{" "}
                                rejected:
                                {item.reason}
                              </div>
                            ))}
                          {semanticOutputImport.rejected.length > 4 && (
                            <div>
                              +{semanticOutputImport.rejected.length - 4} more
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                  <div className="mt-3 space-y-2">
                    {preparedRun.agent_runs.map((agentRun) => {
                      const stepId = agentRun.step_id;
                      const result = executionResults[stepId];
                      const validation = validationResults[stepId];
                      const materialized = materializeResults[stepId];
                      const isExecuting = busyAction === `execute-${stepId}`;
                      const isValidating = busyAction === `validate-${stepId}`;
                      const isMaterializing =
                        busyAction === `materialize-${stepId}`;
                      const requiredArtifacts =
                        agentRun.required_artifacts ?? [];
                      const disableAgentActions =
                        taskRunActionBusy || agentRunActionBusy;
                      return (
                        <div
                          key={agentRun.run_id}
                          className="rounded-md border border-outline-variant/30 bg-surface-container px-2.5 py-2"
                        >
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <div className="min-w-0">
                              <p className="font-medium text-on-surface">
                                {stepId}
                              </p>
                              <p className="break-words font-data text-[11px] text-on-surface-variant">
                                {agentRun.provider} / {agentRun.run_id}
                              </p>
                            </div>
                            <button
                              onClick={() => executePreparedAgentRun(stepId)}
                              disabled={disableAgentActions}
                              className="inline-flex items-center gap-1.5 rounded bg-primary px-2.5 py-1.5 text-xs font-medium text-on-primary transition-opacity hover:opacity-90 disabled:opacity-50"
                            >
                              {isExecuting ? (
                                <Loader2 size={12} className="animate-spin" />
                              ) : (
                                <PlayCircle size={12} />
                              )}
                              Execute
                            </button>
                            <button
                              onClick={() =>
                                validatePreparedAgentRun(
                                  stepId,
                                  requiredArtifacts,
                                )
                              }
                              disabled={
                                disableAgentActions ||
                                requiredArtifacts.length === 0
                              }
                              className="inline-flex items-center gap-1.5 rounded bg-surface px-2.5 py-1.5 text-xs font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
                            >
                              {isValidating ? (
                                <Loader2 size={12} className="animate-spin" />
                              ) : (
                                <Search size={12} />
                              )}
                              Validate
                            </button>
                            <button
                              onClick={() =>
                                materializePreparedAgentRun(
                                  stepId,
                                  requiredArtifacts,
                                )
                              }
                              disabled={
                                disableAgentActions ||
                                requiredArtifacts.length === 0
                              }
                              className="inline-flex items-center gap-1.5 rounded bg-surface px-2.5 py-1.5 text-xs font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
                            >
                              {isMaterializing ? (
                                <Loader2 size={12} className="animate-spin" />
                              ) : (
                                <Database size={12} />
                              )}
                              Materialize
                            </button>
                          </div>
                          {requiredArtifacts.length > 0 && (
                            <p className="mt-1 text-on-surface-variant">
                              必需产物: {requiredArtifacts.join(", ")}
                            </p>
                          )}
                          {result && (
                            <div className="mt-2 space-y-1 text-on-surface-variant">
                              <div className="flex flex-wrap gap-2">
                                <span className="rounded bg-surface px-1.5 py-0.5">
                                  {result.status}
                                </span>
                                <span className="rounded bg-surface px-1.5 py-0.5">
                                  exit {result.exit_code ?? "-"}
                                </span>
                                <span className="rounded bg-surface px-1.5 py-0.5">
                                  {result.duration_ms}ms
                                </span>
                              </div>
                              {result.provider_diagnostics && (
                                <div className="rounded bg-surface px-1.5 py-1 font-data text-[10px]">
                                  <span className="text-on-surface">
                                    provider:
                                    {result.provider_diagnostics.provider ||
                                      agentRun.provider}
                                  </span>
                                  <span className="ml-1">
                                    health:
                                    {result.provider_diagnostics
                                      .health_status || "unknown"}
                                  </span>
                                  {result.provider_diagnostics
                                    .prompt_transport && (
                                    <span className="ml-1">
                                      transport:
                                      {
                                        result.provider_diagnostics
                                          .prompt_transport
                                      }
                                    </span>
                                  )}
                                  {result.provider_diagnostics
                                    .command_resolution_source && (
                                    <span className="ml-1">
                                      command:
                                      {
                                        result.provider_diagnostics
                                          .command_resolution_source
                                      }
                                    </span>
                                  )}
                                  {result.provider_diagnostics
                                    .command_resolution_used_fallback && (
                                    <span className="ml-1 text-warning">
                                      fallback
                                    </span>
                                  )}
                                  {result.provider_diagnostics
                                    .command_resolution_reason && (
                                    <span className="ml-1">
                                      reason:
                                      {
                                        result.provider_diagnostics
                                          .command_resolution_reason
                                      }
                                    </span>
                                  )}
                                  {result.provider_diagnostics
                                    .startup_probe_endpoint && (
                                    <span className="ml-1 break-all">
                                      probe:
                                      {
                                        result.provider_diagnostics
                                          .startup_probe_endpoint
                                      }
                                    </span>
                                  )}
                                </div>
                              )}
                            </div>
                          )}
                          {validation && (
                            <div className="mt-2 rounded bg-surface px-2 py-1.5 text-on-surface-variant">
                              <p>
                                Validation: {validation.status} /{" "}
                                {validation.provenance_status}
                              </p>
                              {validation.accepted_artifact_details?.length ? (
                                <div className="mt-1 space-y-0.5 font-data text-[10px]">
                                  {validation.accepted_artifact_details
                                    .slice(0, 3)
                                    .map((item) => (
                                      <div
                                        key={String(
                                          item.artifact ??
                                            item.path ??
                                            item.sha256,
                                        )}
                                      >
                                        {String(item.artifact ?? "artifact")}{" "}
                                        sha:
                                        {String(item.sha256 ?? "").slice(0, 12)}
                                      </div>
                                    ))}
                                </div>
                              ) : null}
                              {validation.rejected_artifacts.length > 0 && (
                                <p className="mt-1 text-amber-400">
                                  Rejected:{" "}
                                  {validation.rejected_artifacts.length}
                                </p>
                              )}
                              {validation.rejected_artifact_details?.length ? (
                                <div className="mt-1 space-y-0.5 font-data text-[10px] text-warning">
                                  {validation.rejected_artifact_details
                                    .slice(0, 3)
                                    .map((item) => (
                                      <div
                                        key={`${String(item.artifact ?? "artifact")}:${String(item.reason ?? "rejected")}`}
                                      >
                                        {String(item.artifact ?? "artifact")}{" "}
                                        rejected:
                                        {String(item.reason ?? "unknown")}
                                      </div>
                                    ))}
                                </div>
                              ) : null}
                            </div>
                          )}
                          {materialized && (
                            <div className="mt-2 rounded bg-surface px-2 py-1.5 text-on-surface-variant">
                              <p>
                                Evidence: {materialized.status} /{" "}
                                {materialized.evidence_count} items
                              </p>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
                  </div>
                </details>
              )}
              {taskRuns.length > 0 && (
                <div
                  aria-label="最近任务运行"
                  className="min-w-0 rounded-xl border border-outline-variant/30 bg-surface/80 p-4 text-xs"
                >
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <p className="font-medium text-on-surface">
                      最近任务运行
                    </p>
                    <span className="rounded bg-surface-container px-1.5 py-0.5 text-[11px] text-on-surface-variant">
                      {taskRuns.length} 条
                    </span>
                  </div>
                  <div
                    aria-label="最近任务运行列表"
                    className="max-h-80 space-y-2 overflow-y-auto pr-1"
                  >
                    {taskRuns.map((run) => (
                      <button
                        key={run.task_run_id}
                        onClick={() => restoreExistingTaskRun(run.task_run_id)}
                        disabled={
                          taskRunActionBusy ||
                          busyAction === `restore-task-run-${run.task_run_id}`
                        }
                        className={`block w-full rounded-md px-2.5 py-2 text-left transition-colors hover:bg-surface-container-high disabled:opacity-50 ${
                          preparedRun?.task_run_id === run.task_run_id
                            ? "bg-surface-container-high"
                            : "bg-surface-container"
                        }`}
                      >
                        <span className="block font-medium text-on-surface">
                          {run.workflow_id}
                        </span>
                        <span className="block break-words font-data text-[11px] text-on-surface-variant">
                          {busyAction === `restore-task-run-${run.task_run_id}`
                            ? "restoring..."
                            : run.task_run_id}
                        </span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
            </div>
          </Panel>
        )}

        {activeWorkbenchView === "knowledge" && (
          <>
            <Panel title="测试语义库" icon={<Library size={16} />}>
              <div className="space-y-3">
                <div className="rounded-lg border border-outline-variant/30 bg-surface p-3">
                  <div className="grid gap-2 sm:grid-cols-2">
                    <label className="block">
                      <span className="mb-1 block text-xs text-on-surface-variant">
                        特性
                      </span>
                      <input
                        aria-label="Semantic feature"
                        value={semanticFeature}
                        onChange={(event) =>
                          setSemanticFeature(event.target.value)
                        }
                        className="w-full rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                      />
                    </label>
                    <label className="block">
                      <span className="mb-1 block text-xs text-on-surface-variant">
                        模块
                      </span>
                      <input
                        aria-label="Semantic module"
                        value={semanticModule}
                        onChange={(event) =>
                          setSemanticModule(event.target.value)
                        }
                        className="w-full rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                      />
                    </label>
                  </div>
                  <label className="mt-2 block">
                    <span className="mb-1 block text-xs text-on-surface-variant">
                      已有用例，每行一个
                    </span>
                    <textarea
                      aria-label="Semantic case lines"
                      value={semanticLines}
                      onChange={(event) => setSemanticLines(event.target.value)}
                      className="h-24 w-full resize-y rounded-lg border border-outline-variant/30 bg-surface-container p-3 text-xs text-on-surface outline-none focus:border-primary"
                    />
                  </label>
                  <button
                    onClick={buildSemanticCasesFromText}
                    disabled={taskRunActionBusy || !semanticLines.trim()}
                    className="mt-2 inline-flex items-center justify-center gap-2 rounded-lg bg-surface-container-high px-3 py-2 text-sm text-on-surface transition-colors hover:bg-surface disabled:opacity-50"
                  >
                    {busyAction === "build-semantic-cases" ? (
                      <Loader2 size={14} className="animate-spin" />
                    ) : (
                      <Library size={14} />
                    )}
                    生成语义 JSON
                  </button>
                </div>
                <div className="rounded-lg border border-outline-variant/30 bg-surface p-3">
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                    <input
                      type="file"
                      accept=".json,.jsonl,.ndjson,.csv,.txt,.md"
                      aria-label="Semantic case file"
                      onChange={(event) =>
                        setSemanticFile(event.target.files?.[0] ?? null)
                      }
                      className="min-w-0 flex-1 rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 text-sm text-on-surface file:mr-3 file:rounded file:border-0 file:bg-surface-container-high file:px-2 file:py-1 file:text-xs file:text-on-surface"
                    />
                    <button
                      onClick={importSemanticCaseFile}
                      disabled={taskRunActionBusy || !semanticFile}
                      className="inline-flex items-center justify-center gap-2 rounded-lg bg-surface-container-high px-3 py-2 text-sm text-on-surface transition-colors hover:bg-surface disabled:opacity-50"
                    >
                      {busyAction === "import-semantic-file" ? (
                        <Loader2 size={14} className="animate-spin" />
                      ) : (
                        <Save size={14} />
                      )}
                      导入文件
                    </button>
                  </div>
                  {semanticFile && (
                    <p className="mt-2 break-all font-data text-[11px] text-on-surface-variant">
                      {semanticFile.name}
                    </p>
                  )}
                </div>
                <textarea
                  value={semanticJson}
                  onChange={(event) => setSemanticJson(event.target.value)}
                  className="h-44 max-h-[46vh] w-full resize-y rounded-lg border border-outline-variant/30 bg-surface p-3 font-data text-xs text-on-surface outline-none focus:border-primary"
                  aria-label="Semantic JSON"
                  spellCheck={false}
                />
                <div className="flex flex-col gap-2 sm:flex-row">
                  <button
                    onClick={importSemanticCase}
                    disabled={taskRunActionBusy}
                    className="inline-flex items-center justify-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-on-primary transition-opacity hover:opacity-90 disabled:opacity-50"
                  >
                    <Save size={14} />
                    导入用例
                  </button>
                  <input
                    value={semanticQuery}
                    onChange={(event) => setSemanticQuery(event.target.value)}
                    className="min-w-0 flex-1 rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                    aria-label="Semantic search query"
                  />
                  <button
                    onClick={searchSemanticCases}
                    disabled={taskRunActionBusy || !semanticQuery.trim()}
                    className="inline-flex items-center justify-center gap-2 rounded-lg bg-surface-container-high px-3 py-2 text-sm text-on-surface transition-colors hover:bg-surface disabled:opacity-50"
                  >
                    <Search size={14} />
                    搜索
                  </button>
                </div>
                <div className="space-y-2">
                  {semanticResults.map((item) => (
                    <div
                      key={item.semantic_id}
                      className="rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-xs"
                    >
                      <p className="font-medium text-on-surface">
                        {item.case_id}
                      </p>
                      <p className="mt-1 text-on-surface-variant">
                        {item.scenario}
                      </p>
                    </div>
                  ))}
                </div>
              </div>
            </Panel>

            <Panel title="证据库" icon={<Database size={16} />}>
              <div className="space-y-3">
                <div className="rounded-lg border border-outline-variant/30 bg-surface p-3">
                  <div className="grid gap-2 sm:grid-cols-2">
                    <label className="block">
                      <span className="mb-1 block text-xs text-on-surface-variant">
                        证据主题
                      </span>
                      <input
                        aria-label="Evidence subject"
                        value={manualEvidenceSubject}
                        onChange={(event) =>
                          setManualEvidenceSubject(event.target.value)
                        }
                        className="w-full rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                      />
                    </label>
                    <label className="block">
                      <span className="mb-1 block text-xs text-on-surface-variant">
                        源码路径
                      </span>
                      <input
                        aria-label="Evidence path"
                        value={manualEvidencePath}
                        onChange={(event) =>
                          setManualEvidencePath(event.target.value)
                        }
                        className="w-full rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 font-data text-sm text-on-surface outline-none focus:border-primary"
                      />
                    </label>
                  </div>
                  <label className="mt-2 block">
                    <span className="mb-1 block text-xs text-on-surface-variant">
                      证据说明
                    </span>
                    <textarea
                      aria-label="Evidence text"
                      value={manualEvidenceText}
                      onChange={(event) =>
                        setManualEvidenceText(event.target.value)
                      }
                      className="h-20 w-full resize-y rounded-lg border border-outline-variant/30 bg-surface-container p-3 text-xs text-on-surface outline-none focus:border-primary"
                    />
                  </label>
                  <button
                    onClick={saveManualEvidence}
                    disabled={
                      taskRunActionBusy ||
                      !manualEvidenceSubject.trim() ||
                      !workspaceId.trim() ||
                      !repoPath.trim()
                    }
                    className="mt-2 inline-flex items-center justify-center gap-2 rounded-lg bg-surface-container-high px-3 py-2 text-sm text-on-surface transition-colors hover:bg-surface disabled:opacity-50"
                  >
                    {busyAction === "save-manual-evidence" ? (
                      <Loader2 size={14} className="animate-spin" />
                    ) : (
                      <Save size={14} />
                    )}
                    保存证据
                  </button>
                </div>
                <div className="flex flex-col gap-2 sm:flex-row">
                  <input
                    value={memoryQuery}
                    onChange={(event) => setMemoryQuery(event.target.value)}
                    className="min-w-0 flex-1 rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                    aria-label="Evidence search query"
                  />
                  <button
                    onClick={searchMemory}
                    disabled={taskRunActionBusy || !memoryQuery.trim()}
                    className="inline-flex items-center justify-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-on-primary transition-opacity hover:opacity-90 disabled:opacity-50"
                  >
                    <Search size={14} />
                    搜索证据
                  </button>
                </div>
                <div className="rounded-lg border border-amber-400/20 bg-amber-400/5 px-3 py-2 text-xs text-amber-400">
                  <div className="flex items-start gap-2">
                    <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                    <span>
                      证据库只保存结构化事实；Agent
                      原始输出会作为产物上下文保存，不会直接当作事实复用。
                    </span>
                  </div>
                </div>
                <div className="space-y-2">
                  {memoryResults.map((item) => (
                    <div
                      key={item.evidence_id}
                      className="rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-xs"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="rounded bg-surface-container px-1.5 py-0.5 text-on-surface-variant">
                          {item.kind}
                        </span>
                        <span className="font-medium text-on-surface">
                          {item.subject_key}
                        </span>
                        <span className="text-on-surface-variant">
                          {item.status}
                        </span>
                        {item.source_read_status && (
                          <span className="rounded bg-surface-container px-1.5 py-0.5 text-on-surface-variant">
                            source:{item.source_read_status}
                          </span>
                        )}
                        {item.usable_as_source_evidence !== undefined && (
                          <span
                            className={`rounded px-1.5 py-0.5 ${
                              item.usable_as_source_evidence
                                ? "bg-green-400/10 text-green-500"
                                : "bg-amber-400/10 text-amber-500"
                            }`}
                          >
                            usable:{String(item.usable_as_source_evidence)}
                          </span>
                        )}
                      </div>
                      {item.path && (
                        <p className="mt-1 break-words font-data text-on-surface-variant">
                          {item.path}
                        </p>
                      )}
                      {item.reason && (
                        <p className="mt-1 text-on-surface-variant">
                          {item.reason}
                        </p>
                      )}
                      {(() => {
                        const refs = evidenceAuditRefs(item.provenance ?? {});
                        if (refs.length === 0) return null;
                        return (
                          <div className="mt-2 rounded bg-surface-container px-2 py-1.5">
                            <div className="flex flex-wrap gap-1.5 font-data text-[10px] text-on-surface-variant">
                              {refs.map((ref) => (
                                <span
                                  key={`${ref.label}:${ref.artifact}`}
                                  className="rounded bg-surface px-1.5 py-0.5"
                                  title={
                                    ref.sha256
                                      ? `${ref.artifact} sha:${ref.sha256}`
                                      : ref.artifact
                                  }
                                >
                                  {ref.label}: {ref.artifact}
                                  {ref.sha256
                                    ? ` sha:${ref.sha256.slice(0, 12)}`
                                    : ""}
                                </span>
                              ))}
                            </div>
                          </div>
                        );
                      })()}
                      <div className="mt-2 flex flex-wrap items-center gap-2">
                        <button
                          onClick={() => loadMemorySlices(item.evidence_id)}
                          disabled={taskRunActionBusy}
                          className="inline-flex items-center gap-1 rounded bg-surface-container px-2 py-1 text-[11px] text-on-surface-variant transition-colors hover:bg-surface-container-high disabled:opacity-50"
                        >
                          {busyAction ===
                          `memory-slices-${item.evidence_id}` ? (
                            <Loader2 size={12} className="animate-spin" />
                          ) : (
                            <ClipboardList size={12} />
                          )}
                          源码切片
                        </button>
                        {memorySlices[item.evidence_id] && (
                          <span className="font-data text-[11px] text-on-surface-variant">
                            {memorySlices[item.evidence_id].length} slice(s)
                          </span>
                        )}
                      </div>
                      {memorySlices[item.evidence_id] &&
                        memorySlices[item.evidence_id].length > 0 && (
                          <div className="mt-2 space-y-2 text-on-surface-variant">
                            {memorySlices[item.evidence_id]
                              .slice(0, 3)
                              .map((slice) => (
                                <div
                                  key={slice.slice_id}
                                  className="rounded bg-surface-container px-2 py-1.5"
                                >
                                  <p className="break-words font-data text-[11px]">
                                    {slice.file_path}:{slice.start_line}-
                                    {slice.end_line} sha:
                                    {slice.sha256.slice(0, 12)}
                                    {slice.integrity_status && (
                                      <span
                                        className={`ml-1 ${
                                          slice.integrity_status ===
                                          "verified_current"
                                            ? "text-green-500"
                                            : "text-warning"
                                        }`}
                                      >
                                        {slice.integrity_status}
                                      </span>
                                    )}
                                  </p>
                                  {(slice.current_sha256 ||
                                    slice.validation_error) && (
                                    <p className="mt-1 break-words font-data text-[10px] text-warning">
                                      {slice.current_sha256
                                        ? `current:${slice.current_sha256.slice(0, 12)} `
                                        : ""}
                                      {slice.validation_error || ""}
                                    </p>
                                  )}
                                  <pre className="mt-1 max-h-28 overflow-auto whitespace-pre-wrap break-words font-data text-[10px] text-on-surface">
                                    {slice.excerpt}
                                  </pre>
                                </div>
                              ))}
                          </div>
                        )}
                      {item.source_slices &&
                        item.source_slices.length > 0 &&
                        !memorySlices[item.evidence_id] && (
                          <div className="mt-2 space-y-1 text-on-surface-variant">
                            {item.source_slices.slice(0, 3).map((slice) => (
                              <p
                                key={slice.slice_id}
                                className="break-words font-data text-[11px]"
                              >
                                slice {slice.file_path}:{slice.start_line}-
                                {slice.end_line} sha:
                                {slice.sha256.slice(0, 12)}
                                {slice.integrity_status && (
                                  <span
                                    className={`ml-1 ${
                                      slice.integrity_status ===
                                      "verified_current"
                                        ? "text-green-500"
                                        : "text-warning"
                                    }`}
                                  >
                                    {slice.integrity_status}
                                  </span>
                                )}
                              </p>
                            ))}
                          </div>
                        )}
                    </div>
                  ))}
                </div>
              </div>
            </Panel>
          </>
        )}
      </WorkbenchStageFrame>
    </div>
  );
}
