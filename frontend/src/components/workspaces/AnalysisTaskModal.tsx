"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2, X, Eye, Play, BarChart3 } from "lucide-react";
import type {
  AnalysisObject,
  AnalysisPlan,
  CoverageAnalysis,
  ScopePreview as ScopePreviewT,
} from "@/lib/types";
import { api } from "@/lib/api";
import AnalysisObjectEditor from "./AnalysisObjectEditor";
import FocusOptionsEditor from "./FocusOptions";
import ReportPlanEditor from "./ReportPlanEditor";
import ScopePreviewPanel from "./ScopePreview";

interface Props {
  wsId: string;
  open: boolean;
  onClose: () => void;
  onStarted: (info: {
    analysis_units?: number | null;
    evidence_cards?: number | null;
  }) => void;
}

export default function AnalysisTaskModal({
  wsId,
  open,
  onClose,
  onStarted,
}: Props) {
  const [plan, setPlan] = useState<AnalysisPlan | null>(null);
  const [loadingPlan, setLoadingPlan] = useState(true);
  const [planError, setPlanError] = useState<string | null>(null);

  const [preview, setPreview] = useState<ScopePreviewT | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const [includeCoverageGaps, setIncludeCoverageGaps] = useState(true);
  const [coverageAnalyses, setCoverageAnalyses] = useState<CoverageAnalysis[]>(
    [],
  );

  // Load the default plan whenever the modal opens.
  useEffect(() => {
    if (!open) return;
    setLoadingPlan(true);
    setPlanError(null);
    setPreview(null);
    setPreviewError(null);
    setSubmitError(null);
    api.workspaces
      .defaultAnalysisPlan(wsId)
      .then(setPlan)
      .catch((e: unknown) =>
        setPlanError(e instanceof Error ? e.message : "加载默认方案失败"),
      )
      .finally(() => setLoadingPlan(false));
  }, [open, wsId]);

  // Surface the workspace's analyzed coverage so the user knows what will be
  // auto-included as coverage gap test design.
  useEffect(() => {
    if (!open) return;
    api.coverage
      .list()
      .then((list) =>
        setCoverageAnalyses(list.filter((c) => c.workspace_id === wsId)),
      )
      .catch(() => setCoverageAnalyses([]));
  }, [open, wsId]);

  const latestAnalyzedCoverage = useMemo<CoverageAnalysis | null>(() => {
    const analyzed = coverageAnalyses
      .filter((c) => c.status === "analyzed")
      .sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1));
    return analyzed[0] ?? null;
  }, [coverageAnalyses]);

  const objects = plan?.analysis_objects ?? [];

  const setObjects = useCallback(
    (next: AnalysisObject[]) => {
      setPlan((prev) => (prev ? { ...prev, analysis_objects: next } : prev));
      setPreview(null);
    },
    [setPlan],
  );

  const handleFocusChange = useCallback(
    (next: AnalysisPlan["focus"]) => {
      setPlan((prev) => (prev ? { ...prev, focus: next } : prev));
    },
    [setPlan],
  );

  const handleReportsChange = useCallback(
    (next: AnalysisPlan["reports"]) => {
      setPlan((prev) => (prev ? { ...prev, reports: next } : prev));
    },
    [setPlan],
  );

  const handleGuidanceChange = useCallback(
    (next: string) => {
      setPlan((prev) => (prev ? { ...prev, user_guidance: next } : prev));
    },
    [setPlan],
  );

  const effectivePlan = useMemo<AnalysisPlan | null>(() => {
    if (!plan) return null;
    return {
      ...plan,
      analysis_objects: plan.analysis_objects.filter((o) => o.text.trim()),
    };
  }, [plan]);

  const canStart = useMemo(() => {
    if (!effectivePlan) return false;
    if (effectivePlan.analysis_objects.length === 0) return false;
    if (effectivePlan.reports.filter((r) => r.enabled).length === 0) return false;
    return true;
  }, [effectivePlan]);

  const handlePreview = async () => {
    if (!effectivePlan || effectivePlan.analysis_objects.length === 0) {
      setPreviewError("请至少填写一条分析对象");
      return;
    }
    setPreviewLoading(true);
    setPreviewError(null);
    try {
      const result = await api.workspaces.previewScope(wsId, effectivePlan);
      setPreview(result);
    } catch (e: unknown) {
      setPreviewError(e instanceof Error ? e.message : "范围预览失败");
    } finally {
      setPreviewLoading(false);
    }
  };

  const handleStart = async () => {
    if (!effectivePlan || !canStart) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const resp = await api.workspaces.analyze(wsId, {
        plan: effectivePlan,
        scope_preview: preview ?? undefined,
        include_coverage_gaps: includeCoverageGaps,
      });
      onStarted({
        analysis_units: resp.analysis_units ?? null,
        evidence_cards: resp.evidence_cards ?? null,
      });
      onClose();
    } catch (e: unknown) {
      setSubmitError(e instanceof Error ? e.message : "启动分析失败");
    } finally {
      setSubmitting(false);
    }
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="w-[920px] max-w-[95vw] max-h-[92vh] rounded-2xl bg-surface border border-outline-variant/30 shadow-xl flex flex-col overflow-hidden">
        <header className="flex items-center justify-between px-5 py-3 border-b border-outline-variant/20">
          <div>
            <h2 className="text-base font-semibold text-on-surface">
              生成测试视角报告 · 分析任务
            </h2>
            <p className="text-[11px] text-on-surface-variant/70 mt-0.5">
              定义黑/灰盒测试目标 → 选择焦点与报告 → 预览证据范围 → 启动分析。
              GitNexus 仅用于导航，源码与材料是最终证据。
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-md text-on-surface-variant hover:bg-surface-container"
            disabled={submitting}
          >
            <X size={16} />
          </button>
        </header>

        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-5">
          {loadingPlan ? (
            <div className="flex justify-center py-12">
              <Loader2 size={20} className="animate-spin text-primary" />
            </div>
          ) : planError || !plan ? (
            <div className="rounded-xl bg-error/10 border border-error/30 px-3 py-2 text-xs text-error">
              {planError ?? "无法加载默认方案"}
            </div>
          ) : (
            <>
              <AnalysisObjectEditor
                objects={objects}
                onChange={setObjects}
              />
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
                <FocusOptionsEditor
                  value={plan.focus}
                  onChange={handleFocusChange}
                />
                <ReportPlanEditor
                  reports={plan.reports}
                  onChange={handleReportsChange}
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium text-on-surface">
                  附加说明（可选）
                </label>
                <textarea
                  value={plan.user_guidance}
                  onChange={(e) => handleGuidanceChange(e.target.value)}
                  rows={3}
                  placeholder="可以补充业务背景、外部入口、观测方式或重点风险。该字段只能补充强调，不能覆盖报告结构或质量约束。"
                  className="w-full resize-y rounded-xl border border-outline-variant/30 bg-surface-container-low px-3 py-2 text-sm text-on-surface focus:outline-none focus:border-primary/60"
                />
              </div>
              {/* Coverage gap test design */}
              <div className="rounded-xl border border-outline-variant/30 bg-surface-container-low p-3 space-y-2">
                <label className="flex items-center gap-2 text-sm font-medium text-on-surface cursor-pointer">
                  <input
                    type="checkbox"
                    checked={includeCoverageGaps}
                    onChange={(e) => setIncludeCoverageGaps(e.target.checked)}
                    className="accent-primary"
                  />
                  <BarChart3 size={14} /> 纳入覆盖率缺口测试设计
                </label>
                {includeCoverageGaps ? (
                  latestAnalyzedCoverage ? (
                    <p className="text-[11px] text-on-surface-variant/80">
                      将自动纳入该工作区最新已分析覆盖率：
                      <span className="text-on-surface">
                        {latestAnalyzedCoverage.name}
                      </span>
                      （{latestAnalyzedCoverage.module_count} 模块 ·{" "}
                      {new Date(
                        latestAnalyzedCoverage.updated_at,
                      ).toLocaleDateString("zh-CN")}
                      ）。报告 test_design 将包含「覆盖率缺口驱动测试设计」矩阵。
                    </p>
                  ) : (
                    <p className="text-[11px] text-amber-300/80">
                      未发现该工作区已分析的覆盖率。请先到「覆盖率」页上传并绑定本工作区、点击
                      AI 分析；之后启动分析时会自动纳入。
                    </p>
                  )
                ) : (
                  <p className="text-[11px] text-on-surface-variant/60">
                    本次报告将不包含覆盖率缺口测试设计。
                  </p>
                )}
              </div>

              <ScopePreviewPanel preview={preview} loading={previewLoading} />
              {previewError && (
                <div className="rounded-md bg-error/10 border border-error/30 px-3 py-2 text-xs text-error">
                  {previewError}
                </div>
              )}
              {submitError && (
                <div className="rounded-md bg-error/10 border border-error/30 px-3 py-2 text-xs text-error">
                  {submitError}
                </div>
              )}
            </>
          )}
        </div>

        <footer className="flex items-center justify-between px-5 py-3 border-t border-outline-variant/20">
          <div className="text-[11px] text-on-surface-variant/70">
            {effectivePlan
              ? `已选择 ${effectivePlan.analysis_objects.length} 个分析对象 · ${effectivePlan.reports.filter((r) => r.enabled).length} 份报告`
              : "—"}
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={handlePreview}
              disabled={!effectivePlan || previewLoading || submitting}
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg border border-outline-variant/40 text-on-surface-variant hover:bg-surface-container disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {previewLoading ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <Eye size={14} />
              )}
              预览分析范围
            </button>
            <button
              type="button"
              onClick={handleStart}
              disabled={!canStart || submitting}
              className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-primary text-on-primary hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed font-medium"
            >
              {submitting ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <Play size={14} />
              )}
              启动分析
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
