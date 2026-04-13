"use client";

import { useRef, useEffect } from "react";
import type { LogEntry } from "@/lib/types";

const levelColor: Record<string, string> = {
  info: "text-primary",
  warn: "text-[#FFD080]",
  error: "text-tertiary",
  debug: "text-on-surface-variant/60",
};

export default function LogTerminal({ logs }: { logs: LogEntry[] }) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs.length]);

  return (
    <div className="bg-surface-container-lowest rounded-lg p-4 h-56 overflow-y-auto font-data text-xs leading-relaxed">
      {logs.length === 0 && (
        <span className="text-on-surface-variant/40">
          Waiting for logs...
        </span>
      )}
      {logs.map((log, i) => (
        <div key={i} className="flex gap-3">
          <span className="text-on-surface-variant/40 shrink-0 w-20">
            {new Date(log.timestamp).toLocaleTimeString()}
          </span>
          <span
            className={`uppercase w-12 shrink-0 ${levelColor[log.level] ?? "text-on-surface-variant"}`}
          >
            {log.level}
          </span>
          {log.tool && (
            <span className="text-primary-fixed-dim shrink-0">
              [{log.tool}]
            </span>
          )}
          <span className="text-on-surface/80">{log.message}</span>
        </div>
      ))}
      <div ref={endRef} />
    </div>
  );
}
