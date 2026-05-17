"use client";

import { useEffect, useState, useCallback } from "react";
import {
  RefreshCw,
  Loader2,
  Power,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  HelpCircle,
  Rocket,
  ExternalLink,
} from "lucide-react";
import { api } from "@/lib/api";
import type { ToolInfo } from "@/lib/types";

const STATUS_DISPLAY: Record<
  string,
  { label: string; icon: typeof CheckCircle2; color: string; bg: string }
> = {
  running: {
    label: "运行中",
    icon: CheckCircle2,
    color: "text-green-400",
    bg: "bg-green-400/10",
  },
  stopped: {
    label: "已停止",
    icon: XCircle,
    color: "text-red-400",
    bg: "bg-red-400/10",
  },
  error: {
    label: "异常",
    icon: AlertTriangle,
    color: "text-amber-400",
    bg: "bg-amber-400/10",
  },
  unknown: {
    label: "未知",
    icon: HelpCircle,
    color: "text-on-surface-variant",
    bg: "bg-surface-container-high",
  },
};

export default function ToolsPage() {
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [restartingTool, setRestartingTool] = useState<string | null>(null);

  const loadTools = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.tools.status();
      setTools(data);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "加载工具状态失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadTools();
  }, [loadTools]);

  const handleRestart = useCallback(
    async (name: string) => {
      setRestartingTool(name);
      try {
        const result = await api.tools.restart(name);
        if (result.success) {
          setTimeout(loadTools, 2000);
        }
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : "重启失败");
      } finally {
        setRestartingTool(null);
      }
    },
    [loadTools],
  );

  return (
    <div className="max-w-3xl">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="font-display text-2xl font-bold text-on-surface">
            工具状态
          </h1>
          <p className="text-sm text-on-surface-variant mt-1">
            查看和管理分析工具进程
          </p>
        </div>
        <button
          onClick={loadTools}
          className="flex items-center gap-2 px-3 py-2 text-sm text-on-surface-variant hover:text-on-surface bg-surface-container rounded-lg transition-colors"
        >
          <RefreshCw size={14} />
          刷新
        </button>
      </div>

      {error && (
        <div className="mb-6 px-4 py-3 bg-red-500/10 border border-red-500/20 rounded-lg text-sm text-red-400">
          {error}
        </div>
      )}

      {/* Deployment guide — shown when any tool is not running */}
      {!loading && tools.some((t) => !t.healthy) && (
        <div className="mb-6 bg-surface-container rounded-xl border border-outline-variant/20 p-5">
          <div className="flex items-start gap-3">
            <Rocket size={18} className="text-primary mt-0.5 shrink-0" />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-on-surface mb-1">
                有工具尚未运行
              </p>
              <p className="text-xs text-on-surface-variant mb-3">
                DeepWiki-Open 和 GitNexus 需要单独部署后才能使用。可通过部署系统向导完成一键安装，或手动按文档配置。
              </p>
              <div className="flex flex-wrap gap-2">
                <a
                  href="http://localhost:9000"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-primary text-on-primary rounded-lg hover:opacity-90 transition-opacity"
                >
                  <Rocket size={12} />
                  打开部署向导
                  <ExternalLink size={11} className="opacity-70" />
                </a>
                <a
                  href="https://github.com/AsyncFuncAI/deepwiki-open"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-surface-container-high text-on-surface rounded-lg border border-outline-variant/30 hover:bg-surface-container transition-colors"
                >
                  DeepWiki-Open
                  <ExternalLink size={11} className="opacity-50" />
                </a>
              </div>
            </div>
          </div>
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-24 text-on-surface-variant">
          <Loader2 size={20} className="animate-spin mr-2" />
          加载中...
        </div>
      ) : tools.length === 0 ? (
        <div className="text-center py-16 bg-surface-container rounded-xl border border-outline-variant/20">
          <p className="text-sm text-on-surface-variant mb-3">未检测到工具</p>
          <a
            href="http://localhost:9000"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 px-4 py-2 text-sm font-medium bg-primary text-on-primary rounded-lg hover:opacity-90 transition-opacity"
          >
            <Rocket size={14} />
            前往部署系统
          </a>
        </div>
      ) : (
        <div className="space-y-4">
          {tools.map((tool) => {
            const statusKey = tool.healthy ? "running" : (tool.status || "unknown");
            const display = STATUS_DISPLAY[statusKey] ?? STATUS_DISPLAY.unknown;
            const Icon = display.icon;
            const isRestarting = restartingTool === tool.name;

            return (
              <div
                key={tool.name}
                className="bg-surface-container rounded-xl border border-outline-variant/20 p-5"
              >
                <div className="flex items-start justify-between mb-4">
                  <div className="flex items-center gap-3">
                    <div
                      className={`w-3 h-3 rounded-full ${
                        tool.healthy ? "bg-green-400" : "bg-red-400"
                      }`}
                      role="status"
                      aria-label={tool.healthy ? "运行正常" : "服务异常"}
                    />
                    <div>
                      <h3 className="text-base font-medium text-on-surface">
                        {tool.display_name}
                      </h3>
                      <p className="text-xs text-on-surface-variant mt-0.5">
                        {tool.name}
                      </p>
                    </div>
                  </div>
                  <span
                    className={`flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full ${display.bg} ${display.color}`}
                  >
                    <Icon size={12} />
                    {display.label}
                  </span>
                </div>

                <div className="grid grid-cols-3 gap-3 mb-4">
                  {tool.pid && (
                    <div className="bg-surface rounded-lg px-3 py-2">
                      <p className="text-[10px] text-on-surface-variant uppercase tracking-wider mb-0.5">
                        PID
                      </p>
                      <p className="text-xs text-on-surface font-data">
                        {tool.pid}
                      </p>
                    </div>
                  )}
                  {tool.health_url && (
                    <div className="bg-surface rounded-lg px-3 py-2">
                      <p className="text-[10px] text-on-surface-variant uppercase tracking-wider mb-0.5">
                        健康检查
                      </p>
                      <p className="text-xs text-on-surface font-data truncate">
                        {tool.health_url}
                      </p>
                    </div>
                  )}
                  {tool.last_check && (
                    <div className="bg-surface rounded-lg px-3 py-2">
                      <p className="text-[10px] text-on-surface-variant uppercase tracking-wider mb-0.5">
                        上次检查
                      </p>
                      <p className="text-xs text-on-surface font-data">
                        {new Date(tool.last_check).toLocaleString("zh-CN", {
                          hour: "2-digit",
                          minute: "2-digit",
                          second: "2-digit",
                        })}
                      </p>
                    </div>
                  )}
                </div>

                {tool.message && (
                  <div className="mb-4 px-3 py-2 bg-amber-400/5 border border-amber-400/20 rounded-lg text-xs text-amber-400">
                    {tool.message}
                  </div>
                )}

                <div className="flex items-center gap-2">
                  <button
                    onClick={() => handleRestart(tool.name)}
                    disabled={isRestarting}
                    className="flex items-center gap-2 px-4 py-2 text-sm bg-surface-container-high text-on-surface rounded-lg border border-outline-variant/30 hover:bg-surface-container transition-colors disabled:opacity-50"
                  >
                    {isRestarting ? (
                      <Loader2 size={14} className="animate-spin" />
                    ) : (
                      <Power size={14} />
                    )}
                    {isRestarting ? "重启中..." : "重启"}
                  </button>
                  {!tool.healthy && (
                    <a
                      href="http://localhost:9000"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="flex items-center gap-1.5 px-4 py-2 text-sm text-primary hover:opacity-80 transition-opacity"
                    >
                      <Rocket size={14} />
                      部署向导
                    </a>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
