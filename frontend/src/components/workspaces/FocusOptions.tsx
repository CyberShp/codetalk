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
  { key: "key_flows", label: "External trigger / protocol flow" },
  { key: "exception_propagation", label: "Exception propagation path" },
  { key: "exception_branches", label: "Error and fallback branches" },
  { key: "boundary_values", label: "Boundary values and limits" },
  { key: "state_machine", label: "State transitions" },
  { key: "resource_cleanup", label: "Resource allocation / cleanup" },
  { key: "concurrency", label: "Concurrency / async ordering" },
  { key: "observability", label: "Observable logs / counters / alarms" },
  { key: "long_running_flip", label: "Long-running flip / wraparound" },
  { key: "sfmea", label: "SFMEA input" },
  { key: "cpp_implicit_logic", label: "C/C++ macros / callbacks / switch" },
  { key: "security_risk", label: "Security risk", hint: "Off by default", highlight: false },
];

export default function FocusOptionsEditor({ value, onChange }: Props) {
  return (
    <div className="space-y-2">
      <label className="text-sm font-medium text-on-surface">Test focus directions</label>
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
