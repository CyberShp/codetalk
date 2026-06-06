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
  Activity,
} from "lucide-react";
import { api } from "@/lib/api";
import type { ExternalAgentStartupProbeResult, ToolInfo } from "@/lib/types";

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
  available: {
    label: "available",
    icon: CheckCircle2,
    color: "text-green-400",
    bg: "bg-green-400/10",
  },
  unavailable: {
    label: "unavailable",
    icon: XCircle,
    color: "text-red-400",
    bg: "bg-red-400/10",
  },
  busy: {
    label: "busy",
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
  const [startingTool, setStartingTool] = useState<string | null>(null);
  const [stoppingTool, setStoppingTool] = useState<string | null>(null);
  const [probingTool, setProbingTool] = useState<string | null>(null);
  const [probeResults, setProbeResults] = useState<
    Record<string, ExternalAgentStartupProbeResult>
  >({});

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

  const handleStart = useCallback(
    async (name: string) => {
      setStartingTool(name);
      try {
        await api.tools.start(name);
        setTimeout(loadTools, 2000);
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : "启动失败");
      } finally {
        setStartingTool(null);
      }
    },
    [loadTools],
  );

  const handleStop = useCallback(
    async (name: string) => {
      setStoppingTool(name);
      try {
        await api.tools.stop(name);
        setTimeout(loadTools, 1000);
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : "停止失败");
      } finally {
        setStoppingTool(null);
      }
    },
    [loadTools],
  );

  const handleStartupProbe = useCallback(async (name: string) => {
    setProbingTool(name);
    setError(null);
    try {
      const result = await api.tools.startupProbe(name);
      setProbeResults((current) => ({ ...current, [name]: result }));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Startup probe failed");
    } finally {
      setProbingTool(null);
    }
  }, []);

  return (
    <div className="w-full px-4 xl:px-6">
      <div className="flex flex-col gap-4 mb-6 sm:flex-row sm:items-center sm:justify-between">
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
          className="flex w-full items-center justify-center gap-2 whitespace-nowrap px-3 py-2 text-sm text-on-surface-variant hover:text-on-surface bg-surface-container rounded-lg transition-colors sm:w-auto"
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
      {!loading && tools.some((t) => t.managed !== false && !t.healthy) && (
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
            const managed = tool.managed !== false;
            const statusKey = managed
              ? tool.healthy
                ? "running"
                : tool.status || "unknown"
              : tool.status || (tool.healthy ? "available" : "unavailable");
            const display = STATUS_DISPLAY[statusKey] ?? STATUS_DISPLAY.unknown;
            const Icon = display.icon;
            const isRestarting = restartingTool === tool.name;
            const isStarting = startingTool === tool.name;
            const isStopping = stoppingTool === tool.name;
            const isProbing = probingTool === tool.name;
            const isBusy = isRestarting || isStarting || isStopping || isProbing;
            const probeResult = probeResults[tool.name];

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
                  {!managed ? (
                    <div className="flex flex-col gap-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <div className="text-xs text-on-surface-variant/80">
                          Agent CLI is started on demand by CodeTalk; no long-running process is managed here.
                        </div>
                        <button
                          onClick={() => handleStartupProbe(tool.name)}
                          disabled={isBusy}
                          className="inline-flex items-center gap-2 px-3 py-1.5 text-xs bg-surface-container-high text-on-surface rounded-lg border border-outline-variant/30 hover:bg-surface-container transition-colors disabled:opacity-50"
                        >
                          {isProbing ? (
                            <Loader2 size={13} className="animate-spin" />
                          ) : (
                            <Activity size={13} />
                          )}
                          Startup probe
                        </button>
                      </div>
                      {probeResult && (
                        <div className="rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-xs">
                          <div className="flex flex-wrap items-center gap-2 mb-2">
                            <span
                              className={`inline-flex items-center rounded-full px-2 py-0.5 font-medium ${
                                probeResult.healthy
                                  ? "bg-green-400/10 text-green-500"
                                  : "bg-red-400/10 text-red-500"
                              }`}
                            >
                              {probeResult.status}
                            </span>
                            <span className="text-on-surface-variant">
                              {probeResult.provider}
                            </span>
                          </div>
                          <p className="break-words text-on-surface">
                            {probeResult.message}
                          </p>
                          {probeResult.health?.attempts &&
                            probeResult.health.attempts.length > 0 && (
                              <div className="mt-2 space-y-1">
                                {probeResult.health.attempts.map((attempt, index) => (
                                  <div
                                    key={`${attempt.command ?? "attempt"}-${index}`}
                                    className="flex flex-wrap items-center gap-2 text-on-surface-variant"
                                  >
                                    <code className="max-w-full break-words rounded bg-surface-container px-1.5 py-0.5 font-data text-[11px] text-on-surface">
                                      {attempt.command ?? "unknown command"}
                                    </code>
                                    <span>{attempt.status ?? "unknown"}</span>
                                    {attempt.launch_kind && (
                                      <span>{attempt.launch_kind}</span>
                                    )}
                                    {attempt.reason && (
                                      <span className="break-words">
                                        {attempt.reason}
                                      </span>
                                    )}
                                  </div>
                                ))}
                              </div>
                            )}
                          {probeResult.health?.diagnostic?.summary && (
                            <p className="mt-2 break-words text-on-surface-variant">
                              {probeResult.health.diagnostic.summary}
                            </p>
                          )}
                        </div>
                      )}
                    </div>
                  ) : tool.healthy ? (
                    <>
                      <button
                        onClick={() => handleStop(tool.name)}
                        disabled={isBusy}
                        className="flex items-center gap-2 px-4 py-2 text-sm bg-surface-container-high text-red-400 rounded-lg border border-red-400/20 hover:bg-red-400/5 transition-colors disabled:opacity-50"
                      >
                        {isStopping ? (
                          <Loader2 size={14} className="animate-spin" />
                        ) : (
                          <Power size={14} />
                        )}
                        {isStopping ? "停止中..." : "停止"}
                      </button>
                      <button
                        onClick={() => handleRestart(tool.name)}
                        disabled={isBusy}
                        className="flex items-center gap-2 px-4 py-2 text-sm bg-surface-container-high text-on-surface rounded-lg border border-outline-variant/30 hover:bg-surface-container transition-colors disabled:opacity-50"
                      >
                        {isRestarting ? (
                          <Loader2 size={14} className="animate-spin" />
                        ) : (
                          <Power size={14} />
                        )}
                        {isRestarting ? "重启中..." : "重启"}
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        onClick={() => handleStart(tool.name)}
                        disabled={isBusy}
                        className="flex items-center gap-2 px-4 py-2 text-sm bg-surface-container-high text-green-400 rounded-lg border border-green-400/20 hover:bg-green-400/5 transition-colors disabled:opacity-50"
                      >
                        {isStarting ? (
                          <Loader2 size={14} className="animate-spin" />
                        ) : (
                          <Power size={14} />
                        )}
                        {isStarting ? "启动中..." : "启动"}
                      </button>
                      <a
                        href="http://localhost:9000"
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex items-center gap-1.5 px-4 py-2 text-sm text-primary hover:opacity-80 transition-opacity"
                      >
                        <Rocket size={14} />
                        部署向导
                      </a>
                    </>
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
