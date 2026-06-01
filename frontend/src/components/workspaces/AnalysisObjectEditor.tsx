"use client";

import React, { useMemo } from "react";
import { Plus, Trash2 } from "lucide-react";
import type { AnalysisObject } from "@/lib/types";

interface Props {
  objects: AnalysisObject[];
  onChange: (next: AnalysisObject[]) => void;
}

function makeId(): string {
  return `obj_${Math.random().toString(36).slice(2, 10)}`;
}

function normalizePathHint(path: string): string {
  return path.trim().replace(/[\r\n\t]+/g, "/").replace(/\\/g, "/").replace(/\/+/g, "/").replace(/\/+$/g, "");
}

function inferPathHints(text: string): string[] {
  const matches = text.match(/(?:[A-Za-z]:)?[A-Za-z0-9_.-]+(?:[\\/][A-Za-z0-9_.-]+)+(?:[\\/])?/g) ?? [];
  return Array.from(new Set(matches.map(normalizePathHint).filter(Boolean))).slice(0, 16);
}

const EXAMPLE_TEXT =
  "iscsi target login path\niscsi target logout / disconnect / session cleanup\niscsi target error handling and retry path\nFC login and link recovery";

export default function AnalysisObjectEditor({ objects, onChange }: Props) {
  const textValue = useMemo(
    () => objects.map((o) => o.text).join("\n"),
    [objects],
  );

  const handleTextChange = (text: string) => {
    const lines = text.split(/\r?\n/);
    const next: AnalysisObject[] = lines.map((line, idx) => {
      const trimmed = line.trim();
      const existing = objects[idx];
      return {
        id: existing?.id ?? makeId(),
        text: trimmed,
        kind: existing?.kind ?? "topic",
        priority: existing?.priority ?? "medium",
        path_hints: trimmed
          ? Array.from(
              new Set([...(existing?.path_hints ?? []), ...inferPathHints(trimmed)]),
            ).slice(0, 16)
          : [],
      };
    });
    onChange(next);
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <label className="text-sm font-medium text-on-surface">
          分析对象（每行一个，描述你想分析的业务流程或子系统）
        </label>
        <button
          type="button"
          onClick={() => onChange([])}
          disabled={objects.every((o) => !o.text.trim())}
          className="text-xs text-on-surface-variant/70 hover:text-error disabled:opacity-40"
        >
          清空
        </button>
      </div>
      <textarea
        value={textValue}
        onChange={(e) => handleTextChange(e.target.value)}
        placeholder={EXAMPLE_TEXT}
        rows={Math.max(6, objects.length + 1)}
        className="w-full resize-y rounded-xl border border-outline-variant/30 bg-surface-container-low px-3 py-2 text-sm text-on-surface font-mono leading-relaxed focus:outline-none focus:border-primary/60"
      />
      <p className="text-xs text-on-surface-variant/70 leading-relaxed">
        我们不会展示原始的 GitNexus 模块清单 — 请用自然语言描述你关心的业务流程，
        系统会把每条对象解析到具体的文件、函数与 GitNexus 社区作为证据。
      </p>
      {objects.filter((o) => o.text).length > 0 && (
        <div className="rounded-xl border border-outline-variant/20 bg-surface-container/50 px-3 py-2 space-y-1">
          {objects
            .filter((o) => o.text)
            .map((o, idx) => (
              <div
                key={o.id}
                className="flex items-center gap-3 text-xs text-on-surface-variant"
              >
                <span className="shrink-0 w-5 text-right tabular-nums">
                  {idx + 1}.
                </span>
                <span className="flex-1 truncate" title={o.text}>
                  {o.text}
                </span>
                {(o.path_hints?.length ?? 0) > 0 && (
                  <span
                    className="max-w-[180px] truncate rounded border border-primary/20 px-1.5 py-0.5 text-[10px] text-primary/80"
                    title={o.path_hints?.join("\n")}
                  >
                    {o.path_hints?.[0]}
                  </span>
                )}
                <select
                  value={o.priority}
                  onChange={(e) => {
                    const next = objects.map((row) =>
                      row.id === o.id
                        ? { ...row, priority: e.target.value as AnalysisObject["priority"] }
                        : row,
                    );
                    onChange(next);
                  }}
                  className="rounded-md border border-outline-variant/30 bg-transparent px-1 py-0.5"
                >
                  <option value="high">高</option>
                  <option value="medium">中</option>
                  <option value="low">低</option>
                </select>
                <button
                  type="button"
                  onClick={() => onChange(objects.filter((row) => row.id !== o.id))}
                  className="text-on-surface-variant/50 hover:text-error"
                  title="删除"
                >
                  <Trash2 size={12} />
                </button>
              </div>
            ))}
          <button
            type="button"
            onClick={() => onChange([...objects, { id: makeId(), text: "", kind: "topic", priority: "medium", path_hints: [] }])}
            className="flex items-center gap-1 text-xs text-primary hover:underline mt-1"
          >
            <Plus size={12} /> 追加一行
          </button>
        </div>
      )}
    </div>
  );
}
