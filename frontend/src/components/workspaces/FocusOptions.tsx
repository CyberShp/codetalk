"use client";

import React from "react";
import type { FocusOptions as FocusOptionsT } from "@/lib/types";

interface Props {
  value: FocusOptionsT;
  onChange: (next: FocusOptionsT) => void;
}

const ITEMS: Array<{
  key: keyof FocusOptionsT;
  label: string;
  hint?: string;
  highlight?: boolean;
}> = [
  { key: "key_flows", label: "外部触发与协议流程" },
  { key: "exception_propagation", label: "异常传播路径" },
  { key: "exception_branches", label: "错误与降级分支" },
  { key: "boundary_values", label: "边界值与限制条件" },
  { key: "state_machine", label: "状态切换" },
  { key: "resource_cleanup", label: "资源分配与清理" },
  { key: "concurrency", label: "并发与异步时序" },
  { key: "observability", label: "日志、计数器与告警可观测性" },
  { key: "long_running_flip", label: "长稳翻转与计数回绕" },
  { key: "sfmea", label: "SFMEA 输入" },
  { key: "cpp_implicit_logic", label: "C/C++ 宏、回调与 switch" },
  { key: "security_risk", label: "安全风险", hint: "默认关闭", highlight: false },
];

export default function FocusOptionsEditor({ value, onChange }: Props) {
  return (
    <div className="space-y-2">
      <label className="text-sm font-medium text-on-surface">测试关注方向</label>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        {ITEMS.map((item) => {
          const enabled = value[item.key];
          return (
            <label
              key={item.key}
              className={`flex items-start gap-2 rounded-lg border px-3 py-2 cursor-pointer text-xs ${
                enabled
                  ? "border-primary/40 bg-primary/5"
                  : "border-outline-variant/30 hover:bg-surface-container/40"
              }`}
            >
              <input
                type="checkbox"
                className="mt-0.5 accent-primary"
                checked={Boolean(enabled)}
                onChange={(e) =>
                  onChange({ ...value, [item.key]: e.target.checked })
                }
              />
              <span className="flex-1">
                <span className="block text-on-surface">{item.label}</span>
                {item.hint && (
                  <span className="block text-[10px] text-on-surface-variant/60 mt-0.5">
                    {item.hint}
                  </span>
                )}
              </span>
            </label>
          );
        })}
      </div>
    </div>
  );
}
