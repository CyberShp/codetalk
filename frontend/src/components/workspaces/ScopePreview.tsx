"use client";

import React from "react";
import { AlertTriangle, FileText, Hash } from "lucide-react";
import type { ScopePreview } from "@/lib/types";

interface Props {
  preview: ScopePreview | null;
  loading: boolean;
}

function countFileRoles(
  files: ScopePreview["resolved_objects"][number]["candidate_files"],
): Record<string, number> {
  return files.reduce(
    (acc, file) => {
      const role = file.role ?? "related";
      acc[role] = (acc[role] ?? 0) + 1;
      return acc;
    },
    {} as Record<string, number>,
  );
}

export default function ScopePreviewPanel({ preview, loading }: Props) {
  if (loading) {
    return (
      <div className="rounded-xl border border-outline-variant/30 bg-surface-container-low px-3 py-3 text-xs text-on-surface-variant">
        正在解析分析范围…
      </div>
    );
  }
  if (!preview) {
    return (
      <div className="rounded-xl border border-dashed border-outline-variant/30 bg-surface-container-low px-3 py-3 text-xs text-on-surface-variant/70">
        点击「预览分析范围」可以在启动之前看到解析到的文件、符号与相关 GitNexus 社区。
      </div>
    );
  }

  return (
    <div className="space-y-2 rounded-xl border border-outline-variant/30 bg-surface-container-low px-3 py-3 text-xs">
      <div className="flex items-center justify-between text-on-surface">
        <span className="font-medium">分析范围预览</span>
        <span className="text-on-surface-variant">
          预计分析单元 {preview.estimated_analysis_units} · 证据卡 {preview.estimated_evidence_cards}
        </span>
      </div>
      {!preview.gitnexus_available && (
        <div className="flex items-start gap-2 rounded-md bg-amber-400/10 border border-amber-400/30 px-2 py-1.5 text-amber-500">
          <AlertTriangle size={12} className="mt-0.5" />
          <span>GitNexus 图谱不可用，已退回本地代码搜索；结果可能不完整。</span>
        </div>
      )}
      {preview.warnings.length > 0 && (
        <ul className="list-disc list-inside text-on-surface-variant/80 space-y-0.5">
          {preview.warnings.map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
      )}
      <div className="space-y-2 max-h-56 overflow-y-auto pr-1">
        {preview.resolved_objects.map((obj) => {
          const roleCounts = countFileRoles(obj.candidate_files);
          return (
            <div
              key={obj.object_id}
              className="rounded-lg border border-outline-variant/20 bg-surface-container/40 px-2 py-1.5"
            >
              <div className="font-medium text-on-surface mb-0.5 truncate" title={obj.text}>
                {obj.text || "（空对象）"}
              </div>
              <div className="text-[10px] text-on-surface-variant/70 flex flex-wrap items-center gap-x-3 gap-y-0.5">
                <span className="inline-flex items-center gap-1">
                  <FileText size={10} /> 文件 {obj.candidate_files.length}
                </span>
                <span className="inline-flex items-center gap-1">
                  <Hash size={10} /> 符号 {obj.candidate_symbols.length}
                </span>
                {obj.related_communities.length > 0 && (
                  <span>社区：{obj.related_communities.slice(0, 4).join("、")}</span>
                )}
                {Object.keys(roleCounts).length > 0 && (
                  <span>
                    范围：primary {roleCounts.primary ?? 0} / related {roleCounts.related ?? 0} / external {roleCounts.external ?? 0}
                  </span>
                )}
              </div>
              {obj.warnings.length > 0 && (
                <div className="mt-1 text-[10px] text-amber-500/90">
                  ⚠ {obj.warnings.join(" / ")}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
