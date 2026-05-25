"use client";

import React from "react";
import type { ReportSpec } from "@/lib/types";

interface Props {
  reports: ReportSpec[];
  onChange: (next: ReportSpec[]) => void;
}

export default function ReportPlanEditor({ reports, onChange }: Props) {
  const toggle = (id: string, enabled: boolean) => {
    onChange(reports.map((r) => (r.id === id ? { ...r, enabled } : r)));
  };

  return (
    <div className="space-y-2">
      <label className="text-sm font-medium text-on-surface">报告模板</label>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        {reports.map((r) => (
          <label
            key={r.id}
            className={`flex items-start gap-2 rounded-lg border px-3 py-2 cursor-pointer text-xs ${
              r.enabled
                ? "border-primary/40 bg-primary/5"
                : "border-outline-variant/30 hover:bg-surface-container/40"
            }`}
          >
            <input
              type="checkbox"
              className="mt-0.5 accent-primary"
              checked={r.enabled}
              onChange={(e) => toggle(r.id, e.target.checked)}
            />
            <span className="flex-1">
              <span className="block text-on-surface">{r.title}</span>
              <span className="block text-[10px] text-on-surface-variant/60 mt-0.5">
                模板：{r.template_id}
                {r.custom ? " · 自定义" : ""}
              </span>
            </span>
          </label>
        ))}
      </div>
      <p className="text-[11px] text-on-surface-variant/70">
        模板的质量规则由系统统一控制，无法在此处覆盖；如需自定义报告，请使用结构化字段（标题 / 受众 / 问题 / 输出格式）。
      </p>
    </div>
  );
}
