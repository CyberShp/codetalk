"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import {
  Upload,
  Loader2,
  Trash2,
  Play,
  ChevronDown,
  ChevronUp,
  FileText,
  Download,
  AlertTriangle,
  CheckCircle2,
  BarChart3,
  GitBranch,
  FlaskConical,
  LogIn,
  Search,
} from "lucide-react";
import { api } from "@/lib/api";
import type {
  CoverageAnalysis,
  CoverageDetail,
  CoverageModuleResult,
  CoverageTestScenario,
  Workspace,
} from "@/lib/types";

function pct(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`;
}

function rateColor(rate: number): string {
  if (rate >= 0.8) return "text-green-400";
  if (rate >= 0.6) return "text-amber-400";
  return "text-red-400";
}

function rateBg(rate: number): string {
  if (rate >= 0.8) return "bg-green-500";
  if (rate >= 0.6) return "bg-amber-500";
  return "bg-red-500";
}

function RateBar({ rate, label }: { rate: number; label: string }) {
  return (
    <div className="flex items-center gap-3">
      <span className="text-xs text-on-surface-variant w-16 shrink-0">
        {label}
      </span>
      <div className="flex-1 h-2 bg-surface-container rounded-full overflow-hidden">
        <div
          className={`ct-progress-fill h-full rounded-full transition-all ${rateBg(rate)}`}
          style={{ width: `${Math.min(rate * 100, 100)}%` }}
        />
      </div>
      <span className={`text-sm font-mono w-14 text-right ${rateColor(rate)}`}>
        {pct(rate)}
      </span>
    </div>
  );
}

const STATUS_MAP: Record<string, { label: string; color: string }> = {
  parsed: { label: "已解析", color: "text-blue-400" },
  analyzing: { label: "分析中", color: "text-amber-400" },
  analyzed: { label: "已分析", color: "text-green-400" },
};

const ENTRY_KIND_LABEL: Record<string, string> = {
  cli: "CLI",
  api: "API",
  message: "消息",
  config: "配置",
  file: "文件",
  callback: "注册回调",
  timer: "定时任务",
  service: "服务入口",
  unknown: "未知",
};

function entryKindLabel(kind: string): string {
  return ENTRY_KIND_LABEL[kind] ?? kind;
}

function providerLabel(tool?: string): string {
  if (!tool) return "";
  if (tool === "claude-code") return "claude-code";
  if (tool === "opencode") return "opencode";
  if (tool === "source-registration") return "source-registration";
  return tool;
}

function agentStatusClass(status?: string): string {
  if (status === "available" || status === "ok") {
    return "bg-green-500/10 text-green-300";
  }
  if (status === "timeout" || status === "invalid_output" || status === "error") {
    return "bg-amber-500/15 text-amber-300";
  }
  return "bg-surface-container-high text-on-surface-variant";
}

const ENTRY_TRACE_STATUS_LABEL: Record<string, string> = {
  entry_found: "已确认外部入口",
  source_read_ok_entry_not_found: "源码已读，入口仍需确认",
  source_not_found: "未读到源码窗口",
  workspace_not_bound: "未绑定工作区",
  trace_skipped_by_cap: "超过追踪上限",
  tool_unavailable: "工具不可用",
};

function entryTraceStatusLabel(status?: string): string {
  if (!status) return "入口发现状态未知";
  return ENTRY_TRACE_STATUS_LABEL[status] ?? status;
}

function safeExportName(value: string) {
  return value
    .trim()
    .replace(/[^a-zA-Z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 80) || "coverage-analysis";
}

function csvCell(value: unknown) {
  const text = Array.isArray(value) ? value.join("; ") : String(value ?? "");
  return `"${text.replace(/"/g, '""')}"`;
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

function coverageUploadErrorMessage(error: unknown): string {
  const base = error instanceof Error ? error.message : "上传失败";
  return [
    base,
    "修复建议：请上传 Cobertura XML、JaCoCo XML、HTML 覆盖率报告，或内部函数命中表 CSV/TSV/TXT/XLSX。",
    "内部函数命中表至少需要 function_name + code_location + triggered/hit_count，或中文列：特性名称、模块名称、代码路径、函数名称、是否覆盖、覆盖次数。",
  ].join("\n");
}

function coverageEvidenceLabel(mr: CoverageModuleResult) {
  const target = mr.function_name ?? mr.condition ?? mr.module_path;
  const path = mr.file_path ?? mr.module_path;
  const line = mr.line_start ? `:${mr.line_start}` : "";
  return `${target} @ ${path}${line}`;
}

function coverageRiskScores(mr: CoverageModuleResult) {
  const severity = mr.risk_level === "high" ? 9 : mr.risk_level === "low" ? 4 : 6;
  const occurrence = (mr.hit_count ?? 0) === 0 ? 7 : 3;
  const detection = mr.entry_paths?.length ? 4 : 7;
  return { severity, occurrence, detection, rpn: severity * occurrence * detection };
}

const BLACK_BOX_EXPORT_DIMENSIONS = [
  {
    key: "normal_path",
    label: "normal path",
    trigger: "Use a documented public command, RPC, configuration, or workload that reaches this behavior.",
  },
  {
    key: "invalid_input",
    label: "invalid input",
    trigger: "Send malformed, missing, duplicated, or out-of-range public input and observe the rejected operation.",
  },
  {
    key: "resource_shortage",
    label: "resource shortage",
    trigger: "Run with constrained queues, memory, namespace availability, or backing device capacity.",
  },
  {
    key: "timeout",
    label: "timeout",
    trigger: "Delay or stall the external peer, device, request, or completion path until the public timeout behavior appears.",
  },
  {
    key: "reconnect",
    label: "reconnect",
    trigger: "Disconnect the external client or backing service and reconnect using the same public configuration.",
  },
  {
    key: "concurrency",
    label: "concurrency",
    trigger: "Run parallel clients or operations that contend for the same public resource.",
  },
  {
    key: "recovery",
    label: "recovery",
    trigger: "Restart, reset, or resume the public service after an interrupted operation.",
  },
  {
    key: "performance_degradation",
    label: "performance degradation",
    trigger: "Increase request rate, queue depth, or payload size until latency or throughput degradation is observable.",
  },
] as const;

function spdkTestDirectory(mr: CoverageModuleResult) {
  const text = `${mr.module_path} ${mr.file_path ?? ""}`.toLowerCase();
  if (text.includes("iscsi")) return "test/iscsi_tgt";
  if (text.includes("bdev")) return "test/bdev";
  if (text.includes("blob")) return "test/blobstore";
  if (text.includes("ftl")) return "test/ftl";
  if (text.includes("vhost")) return "test/vhost";
  if (text.includes("nvmf")) return "test/nvmf";
  if (text.includes("thread")) return "test/unit/lib/thread";
  if (text.includes("event") || text.includes("reactor")) return "test/event";
  if (text.includes("rpc") || text.includes("jsonrpc")) return "test/json_config";
  return "test";
}

function buildCoverageReportMarkdown(name: string, results: CoverageModuleResult[]) {
  const lines = [
    `# CodeTalk Coverage Analysis Report: ${name}`,
    "",
    "## Summary",
    `- analyzed gaps: ${results.length}`,
    `- black-box cases: ${results.reduce((sum, item) => sum + (item.black_box_cases?.length ?? 0), 0)}`,
    `- generated_at: ${new Date().toISOString()}`,
    "",
    "## Evidence And Flow",
  ];
  for (const mr of results) {
    const branchFactCard = mr as CoverageModuleResult & {
      branch_fact_card?: { source_evidence?: string[] };
    };
    lines.push(
      "",
      `### ${coverageEvidenceLabel(mr)}`,
      `- coverage_gap: ${mr.file_path ?? mr.module_path}${mr.line_start ? `:${mr.line_start}` : ""}`,
      `- risk: ${mr.risk_level ?? "medium"}`,
      `- scenario: ${mr.scenario ?? "补充未覆盖行为的可观测测试流程。"}`,
      `- expected_behavior: ${mr.expected_behavior ?? "返回文档化结果或受控错误。"}`,
      `- entry_status: ${entryTraceStatusLabel(mr.entry_trace_status)}`,
      `- evidence: ${(branchFactCard.branch_fact_card?.source_evidence ?? [coverageEvidenceLabel(mr)]).join("; ")}`,
      "",
      "#### Black-box Cases",
    );
    for (const testCase of mr.black_box_cases ?? []) {
      lines.push(
        `- ${testCase.title}`,
        `  - preconditions: ${testCase.preconditions ?? ""}`,
        `  - inputs: ${testCase.inputs ?? ""}`,
        `  - expected: ${testCase.expected ?? ""}`,
        `  - observability: ${(testCase.observable_signals ?? []).join(", ")}`,
      );
    }
  }
  return `${lines.join("\n")}\n`;
}

function buildCoverageSfmeaCsv(results: CoverageModuleResult[]) {
  const rows = [[
    "target",
    "risk_category",
    "failure_mode",
    "cause",
    "effect",
    "detection",
    "severity",
    "occurrence",
    "detection_score",
    "rpn",
    "mitigation",
    "evidence",
  ]];
  for (const mr of results) {
    const scores = coverageRiskScores(mr);
    for (const dimension of BLACK_BOX_EXPORT_DIMENSIONS) {
      rows.push([
        mr.function_name ?? mr.module_path,
        dimension.key,
        `${dimension.label} failure in externally observable behavior for ${mr.function_name ?? mr.module_path}`,
        `Coverage gap at ${mr.file_path ?? mr.module_path}${mr.line_start ? `:${mr.line_start}` : ""}; trigger: ${dimension.trigger}`,
        mr.expected_behavior ?? "User-visible flow may return an undocumented result, hang, crash, or leave inconsistent state.",
        (mr.observable_signals ?? ["response/status", "logs", "state"]).join("; "),
        String(scores.severity),
        String(scores.occurrence),
        String(scores.detection),
        String(scores.rpn),
        (mr.black_box_cases?.[0]?.title ?? `Add ${dimension.label} black-box regression test`),
        coverageEvidenceLabel(mr),
      ]);
    }
  }
  return `${rows.map((row) => row.map(csvCell).join(",")).join("\n")}\n`;
}

function buildCoverageBlackBoxJson(name: string, results: CoverageModuleResult[]) {
  const cases = results.flatMap((mr) =>
    BLACK_BOX_EXPORT_DIMENSIONS.map((dimension, index) => {
      const sourceCase = (mr.black_box_cases ?? [])[0];
      return {
        id: `${safeExportName(mr.function_name ?? mr.module_path)}-${dimension.key}-${index + 1}`,
        module_path: mr.module_path,
        function_name: mr.function_name ?? null,
        file_path: mr.file_path ?? null,
        dimension: dimension.key,
        case_type:
          (sourceCase as typeof sourceCase & { case_type?: string } | undefined)?.case_type ??
          "black_box_hypothesis",
        title: `${dimension.label}: ${sourceCase?.title ?? mr.scenario ?? "coverage gap black-box case"}`,
        preconditions:
          sourceCase?.preconditions ??
          "Public entry, service configuration, client tool, or workload capable of reaching the observed behavior is available.",
        inputs: `${sourceCase?.inputs ?? mr.input_conditions ?? "public input/workload"}; ${dimension.trigger}`,
        steps: [
          "Prepare the system through documented public configuration, CLI, RPC, or client workflow.",
          dimension.trigger,
          "Observe externally visible response, logs, counters, service state, and recovery behavior.",
          "Record diagnostics without requiring source modification or direct internal function invocation.",
        ],
        expected:
          sourceCase?.expected ??
          mr.expected_behavior ??
          "The system returns a documented result or controlled error without crash, hang, resource leak, or inconsistent state.",
        observable_signals: sourceCase?.observable_signals ?? mr.observable_signals ?? ["response/status", "logs", "state"],
        suggested_spdk_test_dir: spdkTestDirectory(mr),
        diagnostics: {
          evidence: sourceCase?.evidence ?? coverageEvidenceLabel(mr),
          entry_trace_status: mr.entry_trace_status ?? "",
          evidence_gaps: mr.evidence_gaps ?? [],
        },
      };
    }),
  );
  return JSON.stringify(
    {
      version: "codetalk-coverage-black-box-export-v1",
      name,
      generated_at: new Date().toISOString(),
      dimensions: BLACK_BOX_EXPORT_DIMENSIONS.map((item) => item.key),
      cases,
    },
    null,
    2,
  );
}

function buildCoverageFourPieceJson(name: string, results: CoverageModuleResult[]) {
  const matchText = (mr: CoverageModuleResult) =>
    `${mr.module_path} ${mr.file_path ?? ""} ${mr.function_name ?? ""}`.toLowerCase();
  const filePathText = (mr: CoverageModuleResult) => String(mr.file_path ?? mr.module_path ?? "").toLowerCase();
  const functionNameText = (mr: CoverageModuleResult) => String(mr.function_name ?? "").toLowerCase();
  const buildBundle = (id: string, title: string, matcher: (mr: CoverageModuleResult) => boolean) => {
    const matched = results.filter(matcher);
    const evidence = matched.map((mr) => ({
      target: mr.function_name ?? mr.module_path,
      file_path: mr.file_path ?? mr.module_path,
      line_start: mr.line_start ?? null,
      entry_status: mr.entry_trace_status ?? "unknown",
      evidence_label: coverageEvidenceLabel(mr),
      source_window: mr.source_window ?? null,
      source_evidence: (mr as CoverageModuleResult & { branch_fact_card?: { source_evidence?: string[] } })
        .branch_fact_card?.source_evidence ?? [coverageEvidenceLabel(mr)],
    }));
    const flowSteps = matched.map((mr, index) => ({
      order: index + 1,
      target: mr.function_name ?? mr.module_path,
      step: mr.scenario ?? `Exercise externally observable behavior for ${mr.function_name ?? mr.module_path}.`,
      expected_behavior: mr.expected_behavior ?? "The public flow returns a documented result or controlled error.",
      evidence: coverageEvidenceLabel(mr),
    }));
    const sfmea = matched.flatMap((mr) => {
      const scores = coverageRiskScores(mr);
      return BLACK_BOX_EXPORT_DIMENSIONS.map((dimension) => ({
        target: mr.function_name ?? mr.module_path,
        risk_category: dimension.key,
        failure_mode: `${dimension.label} failure in externally observable behavior for ${mr.function_name ?? mr.module_path}`,
        cause: `Coverage gap at ${mr.file_path ?? mr.module_path}${mr.line_start ? `:${mr.line_start}` : ""}; trigger: ${dimension.trigger}`,
        effect: mr.expected_behavior ?? "User-visible flow may return an undocumented result, hang, crash, or leave inconsistent state.",
        detection: (mr.observable_signals ?? ["response/status", "logs", "state"]).join("; "),
        severity: scores.severity,
        occurrence: scores.occurrence,
        detection_score: scores.detection,
        rpn: scores.rpn,
        mitigation: (mr.black_box_cases?.[0]?.title ?? `Add ${dimension.label} black-box regression test`),
        evidence: coverageEvidenceLabel(mr),
      }));
    });
    const blackBoxCases = matched.flatMap((mr) =>
      BLACK_BOX_EXPORT_DIMENSIONS.map((dimension, index) => {
        const sourceCase = (mr.black_box_cases ?? [])[0];
        return {
          id: `${id}-${safeExportName(mr.function_name ?? mr.module_path)}-${dimension.key}-${index + 1}`,
          dimension: dimension.key,
          module_path: mr.module_path,
          function_name: mr.function_name ?? null,
          file_path: mr.file_path ?? null,
          preconditions:
            sourceCase?.preconditions ??
            "Public SPDK target/client configuration and workload tools are available.",
          steps: [
            "Prepare the system through documented public configuration, CLI, RPC, or client workflow.",
            dimension.trigger,
            "Observe externally visible response, logs, counters, service state, and recovery behavior.",
            "Record diagnostics without requiring source modification or direct internal function invocation.",
          ],
          expected:
            sourceCase?.expected ??
            mr.expected_behavior ??
            "The system returns a documented result or controlled error without crash, hang, resource leak, or inconsistent state.",
          observable_signals: sourceCase?.observable_signals ?? mr.observable_signals ?? ["response/status", "logs", "state"],
          diagnostics: {
            evidence: sourceCase?.evidence ?? coverageEvidenceLabel(mr),
            suggested_spdk_test_dir: spdkTestDirectory(mr),
            evidence_gaps: mr.evidence_gaps ?? [],
          },
        };
      }),
    );
    return {
      id,
      title,
      status: matched.length ? "generated" : "missing_evidence",
      code_evidence: evidence,
      flow_steps: flowSteps,
      sfmea,
      black_box_cases: blackBoxCases,
    };
  };

  const bundles = [
    buildBundle("E01", "NVMe-oF connect 主链路", (mr) => {
      const text = matchText(mr);
      return text.includes("nvmf") && (
        text.includes("qpair") ||
        text.includes("ctrlr") ||
        text.includes("tcp") ||
        text.includes("connect")
      );
    }),
    buildBundle("E02", "NVMe-oF 异常链路", (mr) => {
      const text = matchText(mr);
      return text.includes("nvmf") && (
        text.includes("disconnect") ||
        text.includes("timeout") ||
        text.includes("reset")
      );
    }),
    buildBundle("E03", "iSCSI login 主链路", (mr) => {
      const text = matchText(mr);
      return text.includes("iscsi") && text.includes("login");
    }),
    buildBundle("E04", "iSCSI 异常链路", (mr) => {
      const text = matchText(mr);
      return text.includes("iscsi") && (
        text.includes("reject") ||
        text.includes("logout") ||
        text.includes("redirect") ||
        text.includes("chap")
      );
    }),
    buildBundle("E05", "bdev IO 主链路", (mr) => {
      const filePath = filePathText(mr);
      const functionName = functionNameText(mr);
      return filePath.startsWith("lib/bdev/") || functionName.startsWith("spdk_bdev_") || functionName.startsWith("bdev_");
    }),
    buildBundle("E06", "bdev reset/failover", (mr) => {
      const filePath = filePathText(mr);
      const functionName = functionNameText(mr);
      return filePath.startsWith("lib/bdev/") && (
        functionName.includes("reset") ||
        functionName.includes("complete") ||
        functionName.includes("poll_for_outstanding")
      );
    }),
    buildBundle("E07", "blobstore/FTL 恢复和空间不足", (mr) => {
      const text = matchText(mr);
      return text.includes("lib/blob/") || text.includes("lib/ftl/");
    }),
    buildBundle("E08", "vhost/vfio-user lifecycle", (mr) => {
      const text = matchText(mr);
      return text.includes("lib/vhost/") || text.includes("vfio_user");
    }),
    buildBundle("E09", "reactor/thread/poller 调度", (mr) => {
      const text = matchText(mr);
      return text.includes("lib/thread/") || text.includes("lib/event/reactor");
    }),
    buildBundle("E10", "RPC/config 非法参数和幂等", (mr) => {
      const text = matchText(mr);
      return text.includes("lib/rpc/") || text.includes("lib/jsonrpc/") || text.includes("_rpc") || text.includes("jsonrpc");
    }),
  ];

  return JSON.stringify(
    {
      version: "codetalk-coverage-four-piece-v1",
      name,
      generated_at: new Date().toISOString(),
      bundles,
    },
    null,
    2,
  );
}

function buildCoverageRejudgeJson(name: string, results: CoverageModuleResult[]) {
  const highRpnRows = results.flatMap((mr) => {
    const scores = coverageRiskScores(mr);
    return BLACK_BOX_EXPORT_DIMENSIONS
      .map((dimension) => {
        const evidence = coverageEvidenceLabel(mr);
        const sourceCase = (mr.black_box_cases ?? [])[0];
        const steps = [
          "Prepare the system through documented public configuration, CLI, RPC, or client workflow.",
          dimension.trigger,
          "Observe externally visible response, logs, counters, service state, and recovery behavior.",
        ];
        const serializedSteps = steps.join("\n");
        const whiteBoxBoundaryIssue = /\b(call|invoke)\s+spdk_|直接调用内部函数|修改源码/i.test(serializedSteps);
        const evidenceLooksReal = Boolean(mr.file_path || mr.module_path) && /^(lib|test)\//.test(String(mr.file_path ?? mr.module_path));
        const mappedTestDir = spdkTestDirectory(mr);
        const score =
          (evidenceLooksReal ? 25 : 8) +
          (mr.scenario || mr.expected_behavior ? 18 : 8) +
          (sourceCase || mr.black_box_cases?.length ? 18 : 12) +
          (!whiteBoxBoundaryIssue ? 20 : 6) +
          (mappedTestDir !== "test" ? 9 : 3) +
          (scores.rpn >= 100 ? 10 : 6);
        return {
          id: `${safeExportName(mr.function_name ?? mr.module_path)}-${dimension.key}`,
          target: mr.function_name ?? mr.module_path,
          dimension: dimension.key,
          rpn: scores.rpn,
          severity: scores.severity,
          occurrence: scores.occurrence,
          detection_score: scores.detection,
          evidence,
          evidence_real_path: evidenceLooksReal,
          mapped_test_dir: mappedTestDir,
          hallucination_flags: evidenceLooksReal ? [] : ["evidence path is missing or not repository-relative"],
          boundary_issues: whiteBoxBoundaryIssue ? ["black-box steps require internal calls or source modification"] : [],
          omissions: [
            ...(mr.entry_paths?.length ? [] : ["external entry path still needs confirmation"]),
            ...(mr.observable_signals?.length ? [] : ["observable signals are generic and should be sharpened"]),
          ],
          recommendation:
            sourceCase?.title ??
            `Tighten ${dimension.label} case around ${mappedTestDir} with externally observable inputs, logs, metrics, and state checks.`,
          score,
          status: score >= 80 && evidenceLooksReal && !whiteBoxBoundaryIssue ? "pass" : "needs_revision",
        };
      })
      .filter((item) => item.rpn >= 100);
  });

  const averageScore = highRpnRows.length
    ? highRpnRows.reduce((sum, row) => sum + row.score, 0) / highRpnRows.length
    : 0;
  const failedRows = highRpnRows.filter((row) => row.status !== "pass");
  return JSON.stringify(
    {
      version: "codetalk-coverage-rejudge-v1",
      name,
      generated_at: new Date().toISOString(),
      rubric: {
        evidence_truthfulness: 25,
        flow_completeness: 20,
        sfmea_quality: 20,
        black_box_quality: 20,
        hallucination_control: 10,
        usability: 5,
      },
      summary: {
        high_rpn_count: highRpnRows.length,
        average_score: Number(averageScore.toFixed(1)),
        failed_count: failedRows.length,
        pass: highRpnRows.length > 0 && failedRows.length === 0 && averageScore >= 80,
      },
      high_rpn_rejudgements: highRpnRows,
      gap_report: {
        prompt_gaps: failedRows.filter((row) => row.omissions.length > 0).map((row) => row.id),
        retrieval_gaps: failedRows.filter((row) => !row.evidence_real_path).map((row) => row.id),
        workflow_gaps: failedRows.filter((row) => row.mapped_test_dir === "test").map((row) => row.id),
        ui_gaps: [],
        model_capability_gaps: [],
      },
    },
    null,
    2,
  );
}

const CASE_TYPE_LABEL: Record<string, string> = {
  black_box_ready: "黑盒可执行",
  black_box_hypothesis: "黑盒假设",
  gray_box_required: "需要灰盒辅助",
};

const LEVEL_LABEL: Record<string, string> = {
  high: "高",
  medium: "中",
  low: "低",
};

function levelLabel(level?: string): string {
  if (!level) return "";
  return LEVEL_LABEL[level] ?? level;
}

function caseTypeLabel(type?: string): string {
  if (!type) return "";
  return CASE_TYPE_LABEL[type] ?? type;
}

function TestScenarioCard({ scenario }: { scenario: CoverageTestScenario }) {
  return (
    <div className="ct-interactive-card rounded-lg bg-surface-container-high/70 p-3 space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs px-2 py-0.5 rounded-full bg-primary/15 text-primary">
          {caseTypeLabel(scenario.case_type)}
        </span>
        <span className="text-xs px-2 py-0.5 rounded-full bg-surface-container text-on-surface-variant">
          优先级：{levelLabel(scenario.priority)}
        </span>
        <span className="text-xs px-2 py-0.5 rounded-full bg-surface-container text-on-surface-variant">
          置信度：{levelLabel(scenario.confidence)}
        </span>
      </div>
      <div className="text-sm font-medium text-on-surface">
        {scenario.flow_purpose}
      </div>
      <div className="grid gap-2 sm:grid-cols-2 text-[11px] text-on-surface-variant">
        <div><span className="text-on-surface">外部触发：</span>{scenario.external_trigger}</div>
        <div><span className="text-on-surface">输入构造：</span>{scenario.input_construction}</div>
        <div><span className="text-on-surface">正常路径：</span>{scenario.normal_path}</div>
        <div><span className="text-on-surface">异常路径：</span>{scenario.error_path}</div>
        <div><span className="text-on-surface">预期结果：</span>{scenario.expected_result}</div>
        <div>
          <span className="text-on-surface">可观测信号：</span>
          {(scenario.observable_signals ?? []).join("、") || "待补充"}
        </div>
      </div>
      {scenario.gray_box_aid && (
        <div className="text-[11px] text-amber-300/90">
          灰盒辅助：{scenario.gray_box_aid}
        </div>
      )}
      {scenario.sfmea && (
        <div className="text-[11px] text-on-surface-variant/90 border-t border-outline-variant/10 pt-2">
          <span className="text-on-surface">SFMEA：</span>
          故障模式 {scenario.sfmea.failure_mode || "待确认"}；
          触发条件 {scenario.sfmea.trigger_condition || "待确认"}；
          传播影响 {scenario.sfmea.propagation_effect || "待确认"}；
          可观测现象 {scenario.sfmea.observable_effect || "待确认"}；
          推荐测试 {scenario.sfmea.recommended_test || "待确认"}
        </div>
      )}
      {(scenario.evidence_refs?.length || scenario.verification_gaps?.length) ? (
        <div className="text-[10px] text-on-surface-variant/70">
          {scenario.evidence_refs?.length ? `证据：${scenario.evidence_refs.join("、")}` : ""}
          {scenario.verification_gaps?.length ? ` 待确认：${scenario.verification_gaps.join("、")}` : ""}
        </div>
      ) : null}
    </div>
  );
}

/**
 * Structured render of the coverage-test-design-v1 enrichment for one uncovered
 * function: trigger conditions, external entry paths, black-box cases, gray-box
 * scheme, and the evidence / pending-verification gaps.
 */
function GapDesignDetail({ mr }: { mr: CoverageModuleResult }) {
  const triggers = mr.trigger_branches ?? [];
  const entries = mr.entry_paths ?? [];
  const scenarios = mr.test_scenarios ?? [];
  const aiAttempted =
    Boolean(mr.ai_generation_status) && mr.ai_generation_status !== "skipped";
  const deterministicFallback =
    mr.deterministic_case_role === "fallback_recommendation";
  const cases = mr.black_box_cases ?? [];
  const gaps = mr.evidence_gaps ?? [];
  const sw = mr.source_window ?? null;
  const grayRequired = mr.gray_box_required ?? false;
  const discovery = mr.entry_discovery ?? null;
  const discoveryCandidates = discovery?.candidate_external_entries ?? [];
  const discoveryReasons = discovery?.unresolved_reasons ?? [];
  const externalAgent = discovery?.external_agent ?? null;
  const externalAgentProviderStatus = externalAgent?.provider_status ?? {};
  const externalAgentWarnings = externalAgent?.warnings ?? [];
  const hasBlackBoxEntry =
    entries.length > 0 ||
    discoveryCandidates.length > 0 ||
    scenarios.some((scenario) => scenario.case_type === "black_box_ready");

  const hasDesign =
    triggers.length > 0 ||
    entries.length > 0 ||
    discoveryCandidates.length > 0 ||
    discoveryReasons.length > 0 ||
    cases.length > 0 ||
    scenarios.length > 0 ||
    gaps.length > 0 ||
    grayRequired;
  if (!hasDesign) return null;

  return (
    <div className="mt-3 space-y-3">
      {/* Status badges */}
      <div className="flex flex-wrap items-center gap-2">
        {grayRequired ? (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-300 text-xs">
            <FlaskConical size={12} /> 需要灰盒辅助
          </span>
        ) : hasBlackBoxEntry ? (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-green-500/15 text-green-300 text-xs">
            <LogIn size={12} /> 黑盒可触达
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-surface-container-high text-on-surface-variant text-xs">
            <Search size={12} /> 入口待确认
          </span>
        )}
        {sw?.available && (
          <span className="text-xs text-on-surface-variant/70">
            源码 {sw.path}
            {sw.start ? `:${sw.start}-${sw.end}` : ""}
          </span>
        )}
      </div>

      {discovery && (
        <div className="rounded-lg bg-surface-container-high/50 p-3 space-y-2">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="font-medium text-on-surface">入口发现</span>
            <span className="px-1.5 py-0.5 rounded bg-primary/10 text-primary">
              {entryTraceStatusLabel(discovery.entry_trace_status ?? mr.entry_trace_status)}
            </span>
            {discovery.source_verification_status && (
              <span className="text-on-surface-variant/70">
                {discovery.source_verification_status}
              </span>
            )}
            {externalAgent?.status && (
              <span className={`px-1.5 py-0.5 rounded ${agentStatusClass(externalAgent.status)}`}>
                Agent {externalAgent.status}
              </span>
            )}
          </div>
          {(Object.keys(externalAgentProviderStatus).length > 0 ||
            externalAgentWarnings.length > 0) && (
            <div className="space-y-1 rounded-md bg-surface-container/50 px-2 py-1.5">
              {Object.keys(externalAgentProviderStatus).length > 0 && (
                <div className="flex flex-wrap gap-1 text-[10px]">
                  {Object.entries(externalAgentProviderStatus).map(([provider, status]) => (
                    <span
                      key={`${provider}-${status}`}
                      className={`px-1.5 py-0.5 rounded ${agentStatusClass(status)}`}
                    >
                      {providerLabel(provider)}={status}
                    </span>
                  ))}
                </div>
              )}
              {externalAgentWarnings.length > 0 && (
                <ul className="space-y-0.5">
                  {externalAgentWarnings.slice(0, 3).map((warning, i) => (
                    <li key={`agent-warning-${i}`} className="text-[10px] text-amber-200/90">
                      {warning}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
          {discoveryCandidates.length > 0 && (
            <ul className="space-y-1">
              {discoveryCandidates.slice(0, 4).map((candidate, i) => (
                <li
                  key={`disc-${i}-${candidate.entry_symbol ?? ""}`}
                  className="text-xs text-on-surface-variant"
                >
                  <span className="px-1.5 py-0.5 rounded bg-green-500/10 text-green-300 text-[10px] mr-1">
                    {entryKindLabel(candidate.entry_type)}
                  </span>
                  <span className="text-on-surface">
                    {candidate.entry_label ?? candidate.entry_symbol ?? "外部入口候选"}
                  </span>
                  {candidate.confidence && (
                    <span className="opacity-60"> · {candidate.confidence}</span>
                  )}
                  <div className="mt-1 flex flex-wrap gap-1 text-[10px]">
                    {(candidate.provider || candidate.tool) && (
                      <span className="rounded bg-surface-container-high px-1.5 py-0.5 text-on-surface-variant">
                        {providerLabel(candidate.provider ?? candidate.tool)}
                      </span>
                    )}
                    {candidate.turn_id && (
                      <span className="rounded bg-surface-container-high px-1.5 py-0.5 text-on-surface-variant">
                        {candidate.turn_id}
                      </span>
                    )}
                    {candidate.source_verification && (
                      <span className="rounded bg-primary/10 px-1.5 py-0.5 text-primary">
                        {candidate.source_verification}
                      </span>
                    )}
                    {candidate.validation_error && (
                      <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-amber-300">
                        {candidate.validation_error}
                      </span>
                    )}
                  </div>
                  {candidate.evidence && (
                    <div className="opacity-60 mt-0.5 font-mono">{candidate.evidence}</div>
                  )}
                  {candidate.external_trigger &&
                    candidate.external_trigger !== candidate.entry_label && (
                      <div className="opacity-70 mt-0.5">
                        trigger: {candidate.external_trigger}
                      </div>
                    )}
                  {(candidate.entry_file || (candidate.chain && candidate.chain.length > 0)) && (
                    <div className="mt-1 space-y-0.5 font-mono text-[10px] text-on-surface-variant/80">
                      {candidate.entry_file && <div>{candidate.entry_file}</div>}
                      {candidate.chain && candidate.chain.length > 0 && (
                        <div>{candidate.chain.join(" → ")}</div>
                      )}
                    </div>
                  )}
                  {candidate.input_hints && candidate.input_hints.length > 0 && (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {candidate.input_hints.slice(0, 4).map((hint, hintIndex) => (
                        <span
                          key={`hint-${i}-${hintIndex}`}
                          className="rounded bg-surface-container-high px-1.5 py-0.5 text-[10px] text-on-surface"
                        >
                          {hint}
                        </span>
                      ))}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
          {discoveryReasons.length > 0 && (
            <ul className="space-y-1">
              {discoveryReasons.slice(0, 3).map((reason, i) => (
                <li key={`disc-reason-${i}`} className="text-xs text-amber-200/90">
                  {reason}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* Trigger conditions / branches */}
      {triggers.length > 0 && (
        <div>
          <div className="text-xs font-medium text-on-surface mb-1 flex items-center gap-1">
            <GitBranch size={12} /> 触发条件 / 分支
          </div>
          <ul className="space-y-1">
            {triggers.map((b, i) => (
              <li
                key={`trig-${i}-${b.file ?? ""}-${b.line_number ?? ""}`}
                className="text-xs text-on-surface-variant"
              >
                <span className="px-1.5 py-0.5 rounded bg-surface-container-high text-[10px] mr-1">
                  {b.source === "caller" ? "调用点守卫" : "函数内"}
                </span>
                <code className="text-on-surface">{b.condition}</code>
                {b.file && (
                  <span className="opacity-60">
                    {" "}
                    · {b.file}
                    {b.line_number ? `:${b.line_number}` : ""}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* External entry paths (entry-oriented tracing) */}
      {entries.length > 0 ? (
        <div>
          <div className="text-xs font-medium text-on-surface mb-1">
            外部入口路径（入口导向分层追踪）
          </div>
          <ul className="space-y-1">
            {entries.map((e, i) => (
              <li
                key={`entry-${i}-${e.entry_symbol ?? ""}`}
                className="text-xs text-on-surface-variant"
              >
                <span className="px-1.5 py-0.5 rounded bg-primary/15 text-primary text-[10px] mr-1">
                  {entryKindLabel(e.entry_kind)}
                </span>
                {e.tool && (
                  <span className="px-1.5 py-0.5 rounded bg-surface-container-high text-[10px] mr-1">
                    {providerLabel(e.tool)}
                  </span>
                )}
                <span className="font-mono text-on-surface">
                  {(e.chain ?? []).join(" → ")}
                </span>
                {e.evidence && (
                  <div className="opacity-60 mt-0.5 font-mono">{e.evidence}</div>
                )}
                {e.input_hints && e.input_hints.length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {e.input_hints.slice(0, 4).map((hint, hintIndex) => (
                      <span
                        key={`entry-hint-${i}-${hintIndex}-${hint}`}
                        className="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary"
                      >
                        {hint}
                      </span>
                    ))}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </div>
      ) : grayRequired && mr.gray_box ? (
        <div className="text-xs text-amber-300/90">
          确定性追踪未确认外部入口，入口发现仍需验证；必要时使用灰盒辅助方案：{mr.gray_box.scheme}
          {mr.gray_box.injection_points && mr.gray_box.injection_points.length > 0 && (
            <div className="opacity-70 mt-0.5">
              注入点：{mr.gray_box.injection_points.join("；")}
            </div>
          )}
        </div>
      ) : null}

      {scenarios.length > 0 && (
        <div>
          <div className="text-xs font-medium text-on-surface mb-1">AI 生成的测试场景</div>
          <div className="space-y-2">
            {scenarios.map((scenario) => (
              <TestScenarioCard key={scenario.scenario_id} scenario={scenario} />
            ))}
          </div>
        </div>
      )}

      {aiAttempted && scenarios.length === 0 && (
        <div className="rounded-lg border border-amber-400/30 bg-amber-500/10 p-3 text-xs text-amber-200">
          {deterministicFallback
            ? "AI 没有生成通过结构校验和反白盒校验的推荐用例；当前已回退展示确定性 coverage 推荐，请按下方测试用例或灰盒辅助方案执行，并结合证据缺口复核。"
            : "AI 没有生成通过结构校验和反白盒校验的推荐用例。当前只展示覆盖率证据、源码窗口和缺口原因，不把确定性模板草稿伪装成可执行用例。"}
        </div>
      )}

      {/* Black-box / gray-box test cases */}
      {cases.length > 0 && (
        <div>
          <div className="text-xs font-medium text-on-surface mb-1">测试用例</div>
          <div className="space-y-2">
            {cases.map((c, i) => (
              <div
                key={`case-${i}-${c.title}`}
                className="rounded-lg bg-surface-container-high/60 p-2 space-y-0.5"
              >
                <div className="text-xs font-medium text-on-surface">
                  {c.title}
                </div>
                {c.preconditions && (
                  <div className="text-[11px] text-on-surface-variant">
                    前置：{c.preconditions}
                  </div>
                )}
                {c.inputs && (
                  <div className="text-[11px] text-on-surface-variant">
                    输入：{c.inputs}
                  </div>
                )}
                {c.steps && c.steps.length > 0 && (
                  <div className="text-[11px] text-on-surface-variant">
                    步骤：{c.steps.join(" → ")}
                  </div>
                )}
                {c.expected && (
                  <div className="text-[11px] text-on-surface-variant">
                    预期：{c.expected}
                  </div>
                )}
                {c.observable_signals && c.observable_signals.length > 0 && (
                  <div className="text-[11px] text-on-surface-variant/80">
                    可观测：{c.observable_signals.join("、")}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Evidence / pending-verification gaps */}
      {gaps.length > 0 && (
        <div>
          <div className="text-xs font-medium text-on-surface mb-1 flex items-center gap-1">
            <AlertTriangle size={12} className="text-amber-400" /> 待验证 / 证据缺口
          </div>
          <ul className="list-disc list-inside space-y-0.5">
            {gaps.map((g) => (
              <li key={g} className="text-[11px] text-on-surface-variant">
                {g}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Tool availability */}
      {mr.tool_status && (
        <div className="text-[10px] text-on-surface-variant/60">
          工具：
          {Object.entries(mr.tool_status)
            .map(([k, v]) => `${k}=${v}`)
            .join(" · ")}
        </div>
      )}
    </div>
  );
}

export default function CoveragePage() {
  const [analyses, setAnalyses] = useState<CoverageAnalysis[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [analyzing, setAnalyzing] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<CoverageDetail | null>(null);
  const [moduleResults, setModuleResults] = useState<CoverageModuleResult[]>(
    [],
  );
  const [expandedModule, setExpandedModule] = useState<string | null>(null);
  const [uploadName, setUploadName] = useState("");
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const autoExpandedRef = useRef(false);

  const applyDetail = useCallback((id: string, d: CoverageDetail) => {
    setDetail(d);
    if (d.analysis_results_json) {
      try {
        setModuleResults(JSON.parse(d.analysis_results_json));
      } catch {
        setModuleResults([]);
      }
    } else {
      setModuleResults([]);
    }
    setExpandedId(id);
  }, []);

  const loadList = useCallback(async () => {
    try {
      const list = await api.coverage.list();
      setAnalyses(list);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadWorkspaces = useCallback(async () => {
    try {
      const list = await api.workspaces.list();
      setWorkspaces(list);
    } catch {
      setWorkspaces([]);
    }
  }, []);

  useEffect(() => {
    loadList();
    loadWorkspaces();
  }, [loadList, loadWorkspaces]);

  useEffect(() => {
    if (!selectedWorkspaceId && workspaces.length === 1) {
      setSelectedWorkspaceId(workspaces[0].id);
    }
  }, [selectedWorkspaceId, workspaces]);

  useEffect(() => {
    if (autoExpandedRef.current || expandedId || analyses.length === 0) {
      return;
    }
    const latest =
      analyses.find((item) => item.status === "analyzed") ?? analyses[0];
    if (!latest) return;
    autoExpandedRef.current = true;
    api.coverage
      .get(latest.id)
      .then((d) => applyDetail(latest.id, d))
      .catch(() => {
        autoExpandedRef.current = false;
      });
  }, [analyses, applyDetail, expandedId]);

  const handleFileSelect = (fileList: FileList | null) => {
    setSelectedFiles(fileList ? Array.from(fileList) : []);
  };

  const handleUpload = async () => {
    if (selectedFiles.length === 0) {
      setError("请先选择覆盖率文件");
      return;
    }
    setUploading(true);
    setError("");
    try {
      await api.coverage.upload(
        selectedFiles,
        uploadName || undefined,
        selectedWorkspaceId || undefined,
      );
      setUploadName("");
      setSelectedFiles([]);
      if (fileInputRef.current) fileInputRef.current.value = "";
      await loadList();
    } catch (e) {
      setError(coverageUploadErrorMessage(e));
    } finally {
      setUploading(false);
    }
  };

  const handleAnalyze = async (id: string) => {
    setAnalyzing(id);
    setError("");
    try {
      const result = await api.coverage.analyze(id);
      let d = await api.coverage.get(id);
      for (let i = 0; d.status === "analyzing" && i < 30; i += 1) {
        await new Promise((resolve) => setTimeout(resolve, 1000));
        d = await api.coverage.get(id);
      }
      setDetail(d);
      if (result.results?.length) {
        setModuleResults(result.results);
      } else if (d.analysis_results_json) {
        setModuleResults(JSON.parse(d.analysis_results_json));
      } else {
        setModuleResults([]);
      }
      applyDetail(id, d);
      await loadList();
    } catch (e) {
      setError(e instanceof Error ? e.message : "分析失败");
    } finally {
      setAnalyzing(null);
    }
  };

  const handleDelete = async (id: string) => {
    if (!window.confirm("确定删除这次覆盖率分析吗？删除后不可恢复。")) {
      return;
    }

    try {
      await api.coverage.delete(id);
      if (expandedId === id) {
        setExpandedId(null);
        setDetail(null);
        setModuleResults([]);
      }
      await loadList();
    } catch (e) {
      setError(e instanceof Error ? e.message : "删除失败");
    }
  };

  const handleExpand = async (id: string) => {
    if (expandedId === id) {
      setExpandedId(null);
      setDetail(null);
      setModuleResults([]);
      return;
    }
    try {
      const d = await api.coverage.get(id);
      applyDetail(id, d);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载详情失败");
    }
  };

  return (
    <div className="w-full px-4 xl:px-6 space-y-6">
      {/* Header */}
      <div className="ct-reveal ct-liquid-glass rounded-[26px] p-5">
        <h1 className="text-2xl font-display font-bold text-on-surface">
          精准测试覆盖率分析
        </h1>
        <p className="text-sm text-on-surface-variant mt-1">
          上传覆盖率报告（XML/HTML/CSV/TSV/TXT/XLSX/XLS），AI 结合工作区报告、源码和工具证据推荐测试用例
        </p>
      </div>

      {/* Upload section */}
      <div className="ct-reveal ct-reveal-delay-1 ct-liquid-glass rounded-[26px] p-5">
        <h2 className="text-sm font-medium text-on-surface mb-3 flex items-center gap-2">
          <Upload size={16} />
          上传覆盖率报告
        </h2>
        <div className="space-y-3">
          <input
            type="text"
            placeholder="分析名称（可选）"
            value={uploadName}
            onChange={(e) => setUploadName(e.target.value)}
            className="w-full px-3 py-2 rounded-lg bg-surface-container border border-outline-variant/30 text-sm text-on-surface placeholder:text-on-surface-variant/50 focus:outline-none focus:ring-1 focus:ring-primary"
          />
          <select
            value={selectedWorkspaceId}
            onChange={(e) => setSelectedWorkspaceId(e.target.value)}
            className="w-full px-3 py-2 rounded-lg bg-surface-container border border-outline-variant/30 text-sm text-on-surface focus:outline-none focus:ring-1 focus:ring-primary"
          >
            <option value="">不绑定工作区</option>
            {workspaces.map((ws) => (
              <option key={ws.id} value={ws.id}>
                {ws.name} - {ws.repo_path}
              </option>
            ))}
          </select>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept=".xml,.html,.htm,.csv,.tsv,.txt,.xlsx,.xls"
              onChange={(e) => handleFileSelect(e.target.files)}
              className="sr-only"
              disabled={uploading}
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading}
              className="ct-interactive-card inline-flex items-center justify-center gap-2 px-4 py-2 rounded-lg bg-surface-container text-on-surface-variant text-sm hover:bg-surface-container-high disabled:opacity-50"
            >
              <Upload size={14} />
              选择文件
            </button>
            <div className="flex-1 min-w-0 text-xs text-on-surface-variant">
              {selectedFiles.length > 0
                ? selectedFiles.map((f) => f.name).join("、")
                : "尚未选择文件"}
            </div>
            <button
              type="button"
              onClick={handleUpload}
              disabled={uploading || selectedFiles.length === 0}
              className="ct-liquid-button inline-flex items-center justify-center gap-2 px-4 py-2 rounded-lg bg-primary text-on-primary text-sm disabled:opacity-50"
            >
              {uploading ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
              上传并解析
            </button>
          </div>
          <p className="text-xs text-on-surface-variant/60">
            支持 Cobertura XML、JaCoCo XML、HTML 覆盖率报告，以及内网函数命中表（CSV/TSV/TXT/XLSX，兼容文本导出的 XLS）。可多文件上传（按模块目录分类）
          </p>
        </div>

        {/* Reserved: intranet API */}
        <div className="mt-4 pt-4 border-t border-outline-variant/15">
          <div className="flex items-center gap-2 text-xs text-on-surface-variant/50">
            <AlertTriangle size={14} />
            <span>
              内网精准测试工具 API 对接（预留） — 待工具方提供接口规范后启用
            </span>
          </div>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div
          role="alert"
          className="whitespace-pre-line bg-error-container/20 text-error rounded-lg px-4 py-3 text-sm"
        >
          {error}
        </div>
      )}

      {/* Analysis list */}
      {loading ? (
        <div className="flex justify-center py-12">
          <Loader2 size={24} className="animate-spin text-primary" />
        </div>
      ) : analyses.length === 0 ? (
        <div className="text-center py-16 text-on-surface-variant">
          <BarChart3 size={48} className="mx-auto mb-4 opacity-30" />
          <p className="text-lg">暂无覆盖率分析</p>
          <p className="text-sm mt-1">上传覆盖率报告文件开始分析</p>
        </div>
      ) : (
        <div className="space-y-3">
          {analyses.map((a, index) => (
            <div
              key={a.id}
              className="ct-interactive-card bg-surface-container-low rounded-xl border border-outline-variant/20 overflow-hidden"
              style={{ animationDelay: `${80 + index * 45}ms` }}
            >
              {/* Summary row */}
              <div className="p-4 flex flex-col gap-4 lg:flex-row lg:items-center">
                <button
                  onClick={() => handleExpand(a.id)}
                  className="flex-1 flex flex-col gap-3 text-left sm:flex-row sm:items-center"
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <FileText size={16} className="text-primary shrink-0" />
                      <span className="font-medium text-on-surface truncate">
                        {a.name}
                      </span>
                      <span
                        className={`text-xs ${STATUS_MAP[a.status]?.color ?? "text-on-surface-variant"}`}
                      >
                        {STATUS_MAP[a.status]?.label ?? a.status}
                      </span>
                    </div>
                    <div className="flex items-center gap-4 mt-1 text-xs text-on-surface-variant">
                      <span>{a.module_count} 个模块</span>
                      <span>格式: {a.source_format}</span>
                      {a.workspace_id && (
                        <span>工作区：{a.workspace_id.slice(0, 8)}</span>
                      )}
                      <span>
                        {new Date(a.created_at).toLocaleString("zh-CN")}
                      </span>
                    </div>
                  </div>

                  {/* Mini rates */}
                  <div className="flex flex-wrap items-center gap-4 shrink-0">
                    <div className="text-center">
                      <div
                        className={`text-sm font-mono ${rateColor(a.overall_line_rate)}`}
                      >
                        {pct(a.overall_line_rate)}
                      </div>
                      <div className="text-[10px] text-on-surface-variant">
                        行
                      </div>
                    </div>
                    <div className="text-center">
                      <div
                        className={`text-sm font-mono ${rateColor(a.overall_branch_rate)}`}
                      >
                        {a.source_format === "internal_function_hits"
                          ? "无数据"
                          : pct(a.overall_branch_rate)}
                      </div>
                      <div className="text-[10px] text-on-surface-variant">
                        分支
                      </div>
                    </div>
                    <div className="text-center">
                      <div
                        className={`text-sm font-mono ${rateColor(a.overall_function_rate)}`}
                      >
                        {pct(a.overall_function_rate)}
                      </div>
                      <div className="text-[10px] text-on-surface-variant">
                        函数
                      </div>
                    </div>
                  </div>

                  {expandedId === a.id ? (
                    <ChevronUp size={16} className="text-on-surface-variant" />
                  ) : (
                    <ChevronDown
                      size={16}
                      className="text-on-surface-variant"
                    />
                  )}
                </button>

                {/* Actions */}
                <div className="flex flex-wrap items-center gap-2 shrink-0">
                  {a.status === "parsed" && (
                    <button
                      onClick={() => handleAnalyze(a.id)}
                      disabled={analyzing === a.id}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-primary/10 text-primary text-sm hover:bg-primary/20 disabled:opacity-50"
                    >
                      {analyzing === a.id ? (
                        <Loader2 size={14} className="animate-spin" />
                      ) : (
                        <Play size={14} />
                      )}
                      AI 分析
                    </button>
                  )}
                  {a.status === "analyzed" && (
                    <button
                      onClick={() => handleAnalyze(a.id)}
                      disabled={analyzing === a.id}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-surface-container text-on-surface-variant text-sm hover:bg-surface-container-high disabled:opacity-50"
                    >
                      {analyzing === a.id ? (
                        <Loader2 size={14} className="animate-spin" />
                      ) : (
                        <Play size={14} />
                      )}
                      重新分析
                    </button>
                  )}
                  <button
                    onClick={() => handleDelete(a.id)}
                    className="p-1.5 rounded-lg text-on-surface-variant hover:bg-error-container/20 hover:text-error"
                    aria-label="删除"
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              </div>

              {/* Expanded detail */}
              {expandedId === a.id && detail && (
                <div className="border-t border-outline-variant/15 p-4 space-y-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      disabled={moduleResults.length === 0}
                      onClick={() =>
                        downloadTextFile(
                          `${safeExportName(a.name)}-analysis-report.md`,
                          buildCoverageReportMarkdown(a.name, moduleResults),
                          "text/markdown;charset=utf-8",
                        )
                      }
                      className="inline-flex items-center gap-1.5 rounded-lg bg-surface-container px-3 py-1.5 text-sm text-on-surface-variant transition-colors hover:bg-surface-container-high disabled:opacity-50"
                    >
                      <Download size={14} />
                      导出分析报告
                    </button>
                    <button
                      type="button"
                      disabled={moduleResults.length === 0}
                      onClick={() =>
                        downloadTextFile(
                          `${safeExportName(a.name)}-sfmea.csv`,
                          buildCoverageSfmeaCsv(moduleResults),
                          "text/csv;charset=utf-8",
                        )
                      }
                      className="inline-flex items-center gap-1.5 rounded-lg bg-surface-container px-3 py-1.5 text-sm text-on-surface-variant transition-colors hover:bg-surface-container-high disabled:opacity-50"
                    >
                      <Download size={14} />
                      导出 SFMEA
                    </button>
                    <button
                      type="button"
                      disabled={moduleResults.length === 0}
                      onClick={() =>
                        downloadTextFile(
                          `${safeExportName(a.name)}-black-box-cases.json`,
                          buildCoverageBlackBoxJson(a.name, moduleResults),
                          "application/json;charset=utf-8",
                        )
                      }
                      className="inline-flex items-center gap-1.5 rounded-lg bg-surface-container px-3 py-1.5 text-sm text-on-surface-variant transition-colors hover:bg-surface-container-high disabled:opacity-50"
                    >
                      <Download size={14} />
                      导出黑盒用例
                    </button>
                    <button
                      type="button"
                      disabled={moduleResults.length === 0}
                      onClick={() =>
                        downloadTextFile(
                          `${safeExportName(a.name)}-four-piece.json`,
                          buildCoverageFourPieceJson(a.name, moduleResults),
                          "application/json;charset=utf-8",
                        )
                      }
                      className="inline-flex items-center gap-1.5 rounded-lg bg-surface-container px-3 py-1.5 text-sm text-on-surface-variant transition-colors hover:bg-surface-container-high disabled:opacity-50"
                    >
                      <Download size={14} />
                      导出四件套
                    </button>
                    <button
                      type="button"
                      disabled={moduleResults.length === 0}
                      onClick={() =>
                        downloadTextFile(
                          `${safeExportName(a.name)}-rejudge.json`,
                          buildCoverageRejudgeJson(a.name, moduleResults),
                          "application/json;charset=utf-8",
                        )
                      }
                      className="inline-flex items-center gap-1.5 rounded-lg bg-surface-container px-3 py-1.5 text-sm text-on-surface-variant transition-colors hover:bg-surface-container-high disabled:opacity-50"
                    >
                      <Download size={14} />
                      导出复判报告
                    </button>
                  </div>
                  {/* Overall rates */}
                  <div className="space-y-2">
                    <RateBar
                      rate={detail.overall_line_rate}
                      label="行覆盖"
                    />
                    {detail.source_format === "internal_function_hits" ? (
                      <div className="text-xs text-on-surface-variant">
                        分支覆盖：无数据（当前文件只包含函数命中信息）
                      </div>
                    ) : (
                      <RateBar
                        rate={detail.overall_branch_rate}
                        label="分支覆盖"
                      />
                    )}
                    <RateBar
                      rate={detail.overall_function_rate}
                      label="函数覆盖"
                    />
                  </div>

                  {/* Module results */}
                  {moduleResults.length > 0 && (
                    <div className="space-y-2">
                      <h3 className="text-sm font-medium text-on-surface flex items-center gap-2">
                        <CheckCircle2 size={14} className="text-green-400" />
                        AI 分析结果
                      </h3>
                      {moduleResults.map((mr) => {
                        const resultId = [
                          mr.module_path,
                          mr.function_name ?? "",
                          mr.file_path ?? "",
                          mr.line_start ?? "",
                        ].join(":");
                        return (
                        <div
                          key={resultId}
                          className="bg-surface-container rounded-lg overflow-hidden"
                        >
                          <button
                            onClick={() =>
                              setExpandedModule(
                                expandedModule === resultId
                                  ? null
                                  : resultId,
                              )
                            }
                            className="w-full px-4 py-3 flex items-center justify-between text-left"
                          >
                            <div>
                              <span className="text-sm font-mono text-on-surface">
                                {mr.function_name ?? mr.module_path}
                              </span>
                              {mr.function_name && (
                                <div className="mt-1 text-xs text-on-surface-variant">
                                  {mr.file_path}
                                  {mr.line_start ? `:${mr.line_start}` : ""}
                                  {mr.risk_level ? ` · 风险：${levelLabel(mr.risk_level)}` : ""}
                                  {mr.confidence ? ` · 证据置信：${levelLabel(mr.confidence)}` : ""}
                                </div>
                              )}
                              {mr.kind === "function" || mr.function_name ? (
                                <div className="flex flex-wrap items-center gap-3 mt-1 text-xs">
                                  <span className="text-red-300">
                                    未覆盖 · hit_count={mr.hit_count ?? 0}
                                  </span>
                                  <span className="text-on-surface-variant">
                                    模块：{mr.module_path}
                                  </span>
                                </div>
                              ) : (
                                <div className="flex flex-wrap items-center gap-3 mt-1 text-xs">
                                  <span className={rateColor(mr.line_rate)}>
                                    行 {pct(mr.line_rate)}
                                  </span>
                                  <span className={rateColor(mr.branch_rate)}>
                                    分支 {pct(mr.branch_rate)}
                                  </span>
                                  <span className={rateColor(mr.function_rate)}>
                                    函数 {pct(mr.function_rate)}
                                  </span>
                                </div>
                              )}
                            </div>
                            {expandedModule === resultId ? (
                              <ChevronUp
                                size={14}
                                className="text-on-surface-variant"
                              />
                            ) : (
                              <ChevronDown
                                size={14}
                                className="text-on-surface-variant"
                              />
                            )}
                          </button>
                          {expandedModule === resultId && (
                            <div className="px-4 pb-4 border-t border-outline-variant/10">
                              {mr.error ? (
                                <p className="text-sm text-error mt-2">
                                  {mr.error}
                                </p>
                              ) : (
                                <>
                                  <GapDesignDetail mr={mr} />
                                  {mr.analysis ? (
                                    <details className="mt-3 group">
                                      <summary className="text-xs text-on-surface-variant/70 cursor-pointer hover:text-on-surface">
                                        原始建议（Markdown）
                                      </summary>
                                      <div className="mt-2 prose prose-sm max-w-none text-on-surface-variant leading-relaxed whitespace-pre-wrap">
                                        {mr.analysis}
                                      </div>
                                    </details>
                                  ) : null}
                                  {!mr.analysis &&
                                    !mr.trigger_branches?.length &&
                                    !mr.entry_paths?.length &&
                                    !mr.black_box_cases?.length && (
                                      <p className="text-sm text-on-surface-variant mt-2">
                                        暂无分析结果
                                      </p>
                                    )}
                                </>
                              )}
                            </div>
                          )}
                        </div>
                        );
                      })}
                    </div>
                  )}

                  {/* No results yet hint */}
                  {moduleResults.length === 0 &&
                    detail.status === "parsed" && (
                      <div className="text-center py-6 text-on-surface-variant text-sm">
                        覆盖率数据已解析，点击「AI 分析」获取测试建议
                      </div>
                    )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
