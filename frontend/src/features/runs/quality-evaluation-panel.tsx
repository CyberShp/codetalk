"use client";

import { AlertTriangle, ChevronDown, Clock3, RefreshCw, Wrench } from "lucide-react";
import { useState } from "react";
import type { PreparedWorkbenchTaskRun, QualityEvaluationAxis, QualityEvaluationReport } from "@/lib/types";

export type EvaluationAvailability = "loading" | "ready" | "pending" | "unavailable";
type AxisName = "accuracy" | "breadth" | "depth";
const axisLabels: Record<AxisName, string> = { accuracy: "Accuracy", breadth: "Breadth", depth: "Depth" };

export function isQualityRepairing(run: PreparedWorkbenchTaskRun) {
  return [run.status, run.execution_status, run.quality_status, run.runtime?.status].some((value) => value === "quality_repairing" || value === "repairable");
}

export function qualityEvaluationPresentation(
  report: QualityEvaluationReport | null,
  availability: EvaluationAvailability,
  run: PreparedWorkbenchTaskRun,
) {
  if (isQualityRepairing(run)) {
    return { qualityLabel: "修复中", qualityTone: "quality_repairing", deliveryLabel: "等待修复", deliveryTone: "pending" };
  }
  if (report?.delivery_status === "not_ready" || report?.repair_summary.terminal_block_reason) {
    return { qualityLabel: "已阻断", qualityTone: "blocked", deliveryLabel: "已阻断", deliveryTone: "blocked" };
  }
  if (report?.delivery_status === "limited") {
    return { qualityLabel: "受限", qualityTone: "warning", deliveryLabel: "受限交付", deliveryTone: "partial" };
  }
  if (report?.delivery_status === "ready") {
    return { qualityLabel: "通过", qualityTone: "passed", deliveryLabel: "可交付", deliveryTone: "ready" };
  }
  if (availability === "pending") {
    return { qualityLabel: "等待评估", qualityTone: "pending", deliveryLabel: "准备中", deliveryTone: "pending" };
  }
  return null;
}

export function QualityEvaluationPanel({
  report,
  availability,
  run,
  onRetry,
  retryBusy,
}: {
  report: QualityEvaluationReport | null;
  availability: EvaluationAvailability;
  run: PreparedWorkbenchTaskRun;
  onRetry: () => void;
  retryBusy: boolean;
}) {
  const repairing = isQualityRepairing(run);
  const runtime = run.runtime;
  const attempt = runtime?.quality_repair_attempt ?? report?.repair_summary.attempt_count ?? 0;
  const maxAttempts = runtime?.quality_repair_max_attempts;
  if (!report) {
    if (availability === "loading") return null;
    return <section className="ct-v2-quality-evaluation is-unavailable" aria-label="独立质量评估"><header><div><h2>独立质量评估</h2><span>{repairing ? "正在自动修复" : availability === "pending" ? "等待评估完成" : "独立质量评估当前不可用"}</span></div>{repairing ? <Wrench size={17} /> : <AlertTriangle size={17} />}</header>{repairing ? <RepairProgress attempt={attempt} maxAttempts={maxAttempts} /> : null}</section>;
  }
  const terminalBlocked = report.delivery_status === "not_ready" || Boolean(report.repair_summary.terminal_block_reason);
  const headline = repairing ? "正在自动修复" : terminalBlocked ? "终态阻断" : report.delivery_status === "limited" ? "受限" : "可交付";
  return <section className={`ct-v2-quality-evaluation is-${terminalBlocked ? "blocked" : report.delivery_status}`} aria-label="独立质量评估">
    <header><div><span>{report.scope === "independent_benchmark" ? "独立基准评估" : "运行内质量审计"}</span><h2>独立质量评估</h2></div><strong className={`is-${terminalBlocked ? "blocked" : report.delivery_status}`}>{headline}</strong></header>
    {terminalBlocked && report.repair_summary.terminal_block_reason ? <div className="ct-v2-quality-evaluation-terminal"><p><AlertTriangle size={14} />{report.repair_summary.terminal_block_reason}</p><button type="button" disabled={retryBusy} onClick={onRetry}><RefreshCw size={14} />{retryBusy ? "正在重新运行" : "重新运行质量修复"}</button></div> : null}
    <div className="ct-v2-quality-evaluation-axes">{(Object.keys(axisLabels) as AxisName[]).map((name) => <QualityAxisRow key={name} name={name} first={report.first_pass[name]} final={report.final_after_auto_repair[name]} />)}</div>
    {repairing ? <RepairProgress attempt={attempt} maxAttempts={maxAttempts} /> : null}
    {!repairing && report.repair_summary.attempt_count > 0 ? <RepairComparison report={report} /> : null}
    {report.limitations.length ? <details className="ct-v2-quality-evaluation-limitations"><summary>限制项（{report.limitations.length}）<ChevronDown size={14} /></summary><ul>{report.limitations.map((item) => <li key={item}>{item}</li>)}</ul></details> : null}
  </section>;
}

function QualityAxisRow({ name, first, final }: { name: AxisName; first: QualityEvaluationAxis; final: QualityEvaluationAxis }) {
  const [expanded, setExpanded] = useState(false);
  const changed = first.status !== final.status || first.numerator !== final.numerator || first.denominator !== final.denominator;
  return <article className={`is-${final.status}`}><button type="button" aria-expanded={expanded} aria-label={`${expanded ? "收起" : "展开"} ${axisLabels[name]} 详情`} onClick={() => setExpanded((value) => !value)}><span>{axisLabels[name]}</span><strong>{axisStatusLabel(final.status)}</strong><small>{formatRatio(final)}{changed ? ` · 首轮 ${formatRatio(first)}` : ""}</small><ChevronDown size={15} /></button>{expanded ? <div className="ct-v2-quality-axis-details"><dl><div><dt>首轮</dt><dd>{axisStatusLabel(first.status)} · {formatRatio(first)}</dd></div><div><dt>修复后</dt><dd>{axisStatusLabel(final.status)} · {formatRatio(final)}</dd></div></dl>{final.critical_misses.length ? <ol className="ct-v2-quality-critical-misses">{final.critical_misses.map((miss, index) => <li key={miss.item_id}><strong>{miss.public_label || publicObligationLabel(name, index + 1)}</strong><span>{miss.reason || publicMissReason(name)}</span><small>下一步：{miss.recommended_action || publicMissAction(name)}</small></li>)}</ol> : null}<ul>{final.metrics.map((metric) => <li key={metric.name}><span>{metric.name}</span><strong>{metric.numerator}/{metric.denominator}</strong></li>)}</ul><dl className="ct-v2-quality-layer-list">{(["L0", "L1", "L2", "L3"] as const).map((layer) => <div key={layer}><dt>{layer}</dt><dd>{layerStatusLabel(final.validation_layers[layer]?.status)}{final.validation_layers[layer]?.limitations?.length ? ` · ${final.validation_layers[layer].limitations.join("；")}` : ""}</dd></div>)}</dl>{final.limitations.length ? <p>{final.limitations.join("；")}</p> : null}</div> : null}</article>;
}
function RepairProgress({ attempt, maxAttempts }: { attempt: number; maxAttempts?: number }) { return <section className="ct-v2-quality-repair-progress" aria-label="自动修复进度"><Wrench size={15} /><div><strong>正在自动修复</strong><span>{maxAttempts ? `第 ${attempt} / ${maxAttempts} 次` : `第 ${attempt} 次`}</span></div><Clock3 size={14} /></section>; }
function RepairComparison({ report }: { report: QualityEvaluationReport }) { return <details className="ct-v2-quality-repair-comparison"><summary>查看自动修复前后对比<ChevronDown size={14} /></summary><dl>{(Object.keys(axisLabels) as AxisName[]).map((name) => <div key={name}><dt>{axisLabels[name]}</dt><dd>{formatRatio(report.first_pass[name])} → {formatRatio(report.final_after_auto_repair[name])}</dd></div>)}</dl></details>; }
function formatRatio(axis: QualityEvaluationAxis) { return `${axis.numerator}/${axis.denominator}`; }
function axisStatusLabel(status: QualityEvaluationAxis["status"]) { return ({ pass: "通过", limited: "受限", fail: "未通过" } as Record<string, string>)[status] || "未检查"; }
function layerStatusLabel(status?: string) { return ({ pass: "通过", fail: "未通过", not_run: "未运行", not_applicable: "不适用" } as Record<string, string>)[status || ""] || "未检查"; }
function publicObligationLabel(name: AxisName, ordinal: number) { return `${({ accuracy: "关键事实", breadth: "关键覆盖项", depth: "关键因果链" } as Record<AxisName, string>)[name]} ${ordinal}`; }
function publicMissReason(name: AxisName) { return ({ accuracy: "公开证据未能闭合该事实陈述", breadth: "关键场景缺少闭环覆盖", depth: "关键因果链缺少闭环验证" } as Record<AxisName, string>)[name]; }
function publicMissAction(name: AxisName) { return ({ accuracy: "核对公开源码证据与事实陈述，并修正不一致内容", breadth: "补充该关键场景及其对应测试证据", depth: "补充入口、状态转换、错误传播和验证结果的闭环证据" } as Record<AxisName, string>)[name]; }
