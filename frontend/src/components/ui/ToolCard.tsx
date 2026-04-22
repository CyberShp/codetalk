"use client";

import { useState } from "react";
import StatusBadge from "./StatusBadge";
import { api } from "@/lib/api";

interface Props {
  name: string;
  description: string;
  capabilities: string[];
  healthy: boolean;
  containerStatus?: string;
  loading?: boolean;
  comingSoon?: boolean;
  onStatusChange?: () => void;
}

export default function ToolCard({
  name,
  description,
  capabilities,
  healthy,
  containerStatus,
  loading,
  comingSoon,
  onStatusChange,
}: Props) {
  const [restarting, setRestarting] = useState(false);
  const [feedback, setFeedback] = useState<{ ok: boolean; msg: string } | null>(null);
  const status = comingSoon
    ? "offline"
    : loading
      ? "checking"
      : containerStatus === "busy"
        ? "busy"
        : containerStatus === "timeout"
          ? "timeout"
          : healthy
            ? "online"
            : "offline";

  const handleRestart = async () => {
    setRestarting(true);
    setFeedback(null);
    try {
      const result = await api.components.restart(name);
      setFeedback({ ok: result.success, msg: result.message });
      if (result.success && onStatusChange) {
        setTimeout(onStatusChange, 3000);
      }
    } catch (e) {
      setFeedback({ ok: false, msg: e instanceof Error ? e.message : "重启失败" });
    } finally {
      setRestarting(false);
    }
  };

  return (
    <div
      className={`relative group bg-surface-container/40 backdrop-blur-xl border border-outline-variant/20 rounded-xl p-5 flex flex-col gap-4 transition-all duration-300 hover:bg-surface-container/60 hover:border-primary/30 hover:shadow-lg ${
        comingSoon ? "grayscale-[0.5] opacity-70" : ""
      }`}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0">
          <h3 className="font-display font-bold text-on-surface text-base truncate group-hover:text-primary transition-colors">
            {name}
          </h3>
          <p className="text-xs text-on-surface-variant mt-1.5 leading-relaxed line-clamp-2 h-8">
            {description}
          </p>
        </div>
        <div className="shrink-0 pt-0.5 flex items-center gap-2">
          {!comingSoon && !loading && (
            <button
              onClick={handleRestart}
              disabled={restarting}
              className="opacity-0 group-hover:opacity-100 transition-opacity px-2 py-1 text-[10px] rounded-md bg-surface-container-high text-on-surface-variant hover:text-on-surface disabled:opacity-40"
            >
              {restarting ? "重启中..." : "重启"}
            </button>
          )}
          <StatusBadge status={status} />
        </div>
      </div>

      {feedback && (
        <p className={`text-[10px] ${feedback.ok ? "text-secondary-fixed-dim" : "text-tertiary"}`}>
          {feedback.msg}
        </p>
      )}
      {!feedback && !comingSoon && !loading && containerStatus && containerStatus !== "running" && (
        <p className="text-[10px] text-on-surface-variant/60">
          状态: {containerStatus}
        </p>
      )}

      <div className="flex flex-wrap gap-1.5 mt-auto pt-2">
        {capabilities.map((cap) => (
          <span
            key={cap}
            className="text-[10px] px-2 py-0.5 rounded-md bg-primary/5 text-primary-fixed-dim border border-primary/10 font-medium"
          >
            {cap}
          </span>
        ))}
      </div>

      {comingSoon && (
        <div className="absolute inset-0 flex items-center justify-center bg-surface-container/20 rounded-xl pointer-events-none">
          <span className="bg-surface-container-high/90 px-3 py-1 rounded-full text-[10px] font-bold text-on-surface-variant border border-outline-variant/30 shadow-sm">
            即将推出
          </span>
        </div>
      )}
    </div>
  );
}
