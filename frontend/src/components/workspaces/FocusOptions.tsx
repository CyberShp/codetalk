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
  { key: "key_flows", label: "关键业务/协议流程" },
  { key: "exception_branches", label: "异常分支" },
  { key: "exception_propagation", label: "异常传播路径" },
  { key: "boundary_values", label: "边界值" },
  { key: "long_running_flip", label: "长运行变量翻转/回绕" },
  { key: "state_machine", label: "状态机/状态转换" },
  { key: "resource_cleanup", label: "资源分配与清理" },
  { key: "concurrency", label: "并发/锁/异步顺序" },
  { key: "observability", label: "可观测性（日志/计数/告警）" },
  { key: "sfmea", label: "SFMEA 输入" },
  { key: "cpp_implicit_logic", label: "C/C++ 隐式逻辑（宏/函数指针/switch）" },
  { key: "security_risk", label: "安全风险", hint: "默认关闭", highlight: false },
];

export default function FocusOptionsEditor({ value, onChange }: Props) {
  return (
    <div className="space-y-2">
      <label className="text-sm font-medium text-on-surface">焦点方向</label>
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
