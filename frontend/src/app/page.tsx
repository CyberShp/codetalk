"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import Link from "next/link";
import gsap from "gsap";
import { useGSAP } from "@gsap/react";
import {
  FolderOpen,
  Plus,
  Wrench,
  RefreshCw,
  Activity,
  Radar,
  ShieldCheck,
  MessageSquareText,
  Workflow,
  FileText,
  ArrowRight,
} from "lucide-react";
import { api } from "@/lib/api";
import type { Workspace } from "@/lib/types";

gsap.registerPlugin(useGSAP);

export default function WorkbenchPage() {
  const homeShellRef = useRef<HTMLDivElement | null>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [loading, setLoading] = useState(true);
  const [sectionErrors, setSectionErrors] = useState<{
    workspaces?: string;
  }>({});

  const loadData = useCallback(async () => {
    setLoading(true);
    setSectionErrors({});
    const wsResult = await api.workspaces.list().then(
      (value) => ({ status: "fulfilled" as const, value }),
      () => ({ status: "rejected" as const }),
    );
    const errs: { workspaces?: string } = {};
    if (wsResult.status === "fulfilled") setWorkspaces(wsResult.value);
    else errs.workspaces = "加载失败，请刷新重试";
    setSectionErrors(errs);
    setLoading(false);
  }, []);

  useEffect(() => {
    queueMicrotask(() => {
      void loadData();
    });
  }, [loadData]);

  useGSAP(
    () => {
      const root = homeShellRef.current;
      if (!root) return;
      if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

      const movePointer = (event: PointerEvent) => {
        const rect = root.getBoundingClientRect();
        gsap.to(root, {
          "--ct-home-x": `${event.clientX - rect.left}px`,
          "--ct-home-y": `${event.clientY - rect.top}px`,
          duration: 0.55,
          ease: "power3.out",
        });
      };

      root.addEventListener("pointermove", movePointer);

      return () => {
        root.removeEventListener("pointermove", movePointer);
      };
    },
    { scope: homeShellRef },
  );

  const indexedWorkspaces = workspaces.filter((workspace) => workspace.indexed === 1).length;
  const reportCount = workspaces.reduce((total, workspace) => total + workspace.reports.length, 0);
  const heroMetrics = [
    { label: "测试工作区", value: loading ? "--" : workspaces.length, icon: FolderOpen },
    { label: "已索引项目", value: loading ? "--" : indexedWorkspaces, icon: ShieldCheck },
    { label: "沉淀报告", value: loading ? "--" : reportCount, icon: Activity },
  ];
  const systemStages = [
    { label: "项目材料", value: "workspace", icon: FolderOpen },
    { label: "AI 线程", value: "context", icon: MessageSquareText },
    { label: "Agent 编排", value: "execute", icon: Workflow },
    { label: "证据报告", value: "evidence", icon: FileText },
  ];

  return (
    <div ref={homeShellRef} className="ct-home-shell w-full px-4 xl:px-6">
      {/* Header */}
      <div className="ct-home-topbar ct-reveal flex flex-col gap-4 mb-6 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="font-display text-xl font-bold text-on-surface sm:text-2xl">
            CodeTalk 工作台
          </h1>
          <p className="text-sm text-on-surface-variant mt-1">
            代码分析与知识工作台
          </p>
        </div>
        <div className="flex w-full items-center gap-3 sm:w-auto">
          <button
            onClick={loadData}
            aria-label="刷新数据"
            className="ct-interactive-card flex flex-1 items-center justify-center gap-2 whitespace-nowrap px-3 py-2 text-sm text-on-surface-variant hover:text-on-surface bg-surface-container rounded-lg transition-colors sm:flex-none"
          >
            <RefreshCw size={14} />
            刷新
          </button>
          <Link
            href="/workspaces/new"
            className="ct-liquid-button flex flex-1 items-center justify-center gap-2 whitespace-nowrap px-4 py-2 text-sm font-medium bg-primary text-on-primary rounded-lg sm:flex-none"
          >
            <Plus size={16} />
            新建工作空间
          </Link>
        </div>
      </div>

      <section className="ct-home-hero ct-command-stage ct-liquid-glass ct-reveal mb-8 rounded-[28px] p-5 sm:p-7 lg:p-8">
        <div className="ct-home-orbit-field" aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
        <div className="relative z-10 grid gap-8 lg:grid-cols-[0.92fr_1.08fr] lg:items-center">
          <div className="ct-home-hero-copy max-w-2xl">
            <div className="ct-home-kicker mb-4 inline-flex items-center gap-2 rounded-full border border-outline-variant/60 bg-white/55 px-3 py-1 text-xs font-medium text-primary shadow-sm backdrop-blur">
              <Radar size={13} />
              AI 测试协同工作台
            </div>
            <h2 className="ct-home-title font-display text-3xl font-bold leading-tight text-on-surface sm:text-4xl lg:text-5xl">
              <span className="ct-home-title-line">把代码理解</span>
              <span className="ct-home-title-line">变成测试行动</span>
            </h2>
            <p className="ct-home-copy mt-4 max-w-xl text-sm leading-6 text-on-surface-variant sm:text-base">
              CodeTalks 把需求、代码、工具执行器和测试证据串成一条 AI 辅助测试流水线；从黑盒设计、覆盖洞察到报告复盘，都在本机工作台完成。
            </p>
            <div className="mt-6 flex flex-col gap-3 sm:flex-row">
              <Link
                href="/ai"
                className="ct-home-primary-action inline-flex items-center justify-center gap-2 rounded-2xl px-4 py-3 text-sm font-semibold"
              >
                <MessageSquareText size={16} />
                打开 AI 线程
                <ArrowRight size={15} />
              </Link>
              <Link
                href="/workbench"
                className="ct-home-secondary-action inline-flex items-center justify-center gap-2 rounded-2xl px-4 py-3 text-sm font-semibold"
              >
                <Workflow size={16} />
                进入智能体编排
              </Link>
            </div>
            <div className="mt-6 grid grid-cols-3 gap-3">
              {heroMetrics.map((item, index) => {
                const Icon = item.icon;
                return (
                  <div
                    key={item.label}
                    className="ct-home-metric ct-interactive-card rounded-2xl border border-white/70 bg-white/50 p-3 shadow-sm backdrop-blur"
                    style={{ animationDelay: `${100 + index * 70}ms` }}
                  >
                    <div className="mb-3 flex h-8 w-8 items-center justify-center rounded-xl bg-primary/10 text-primary">
                      <Icon size={15} />
                    </div>
                    <div className="font-display text-xl font-bold text-on-surface">{item.value}</div>
                    <div className="mt-1 text-xs text-on-surface-variant">{item.label}</div>
                  </div>
                );
              })}
            </div>
          </div>

          <div className="ct-home-product-stage" aria-label="AI 测试中枢视觉面板">
            <div className="ct-home-core">
              <div className="ct-home-core__rings" aria-hidden="true">
                <span />
                <span />
                <span />
              </div>
              <div className="ct-home-core__screen">
                <div className="ct-home-core__topline">
                  <span>CODETALK AI OS</span>
                  <em>local</em>
                </div>
                <div className="ct-home-core__title">
                  <strong>AI 测试中枢</strong>
                  <span>理解上下文 · 编排 Agent · 沉淀证据</span>
                </div>
                <div className="ct-home-core__matrix">
                  {[
                    ["需求语义", "0.92"],
                    ["变更影响", "0.78"],
                    ["黑盒风险", "0.84"],
                    ["报告线索", "0.66"],
                  ].map(([label, score]) => (
                    <div key={label}>
                      <span>{label}</span>
                      <strong>{score}</strong>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            <div className="ct-home-satellite ct-home-satellite-a">
              <Wrench size={15} />
              <span>Agent CLI</span>
              <strong>ready</strong>
            </div>
            <div className="ct-home-satellite ct-home-satellite-b">
              <ShieldCheck size={15} />
              <span>风险切片</span>
              <strong>scoped</strong>
            </div>
            <div className="ct-home-satellite ct-home-satellite-c">
              <FileText size={15} />
              <span>证据报告</span>
              <strong>traceable</strong>
            </div>
          </div>
        </div>

        <div className="ct-home-system-strip relative z-10 mt-8">
          {systemStages.map((stage, index) => {
            const Icon = stage.icon;
            return (
              <div key={stage.label} className="ct-home-system-node" style={{ animationDelay: `${220 + index * 70}ms` }}>
                <div>
                  <Icon size={16} />
                  <span>{stage.label}</span>
                </div>
                <strong>{stage.value}</strong>
                {index < systemStages.length - 1 && <ArrowRight size={14} className="ct-home-system-arrow" />}
              </div>
            );
          })}
        </div>
      </section>

      {/* Workspaces Section */}
      <div className="ct-home-section mb-8">
        <h2 className="text-sm font-medium text-on-surface-variant mb-3 flex items-center gap-2">
          <FolderOpen size={14} />
          工作空间
        </h2>
        {loading ? (
          <div className="grid grid-cols-3 gap-4">
            {[1, 2, 3].map((i) => (
              <div
                key={i}
                className="h-24 bg-surface-container rounded-xl border border-outline-variant/20 animate-pulse"
              />
            ))}
          </div>
        ) : sectionErrors.workspaces ? (
          <div className="px-4 py-3 bg-red-500/10 border border-red-500/20 rounded-lg text-sm text-red-400">
            工作空间{sectionErrors.workspaces}
          </div>
        ) : workspaces.length === 0 ? (
          <div className="ct-premium-empty flex flex-col items-center justify-center h-32 rounded-2xl gap-3">
            <FolderOpen size={28} className="text-on-surface-variant/40" />
            <p className="text-on-surface-variant text-sm">还没有工作空间</p>
            <Link
              href="/workspaces/new"
              className="ct-liquid-button flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-primary text-on-primary rounded-lg"
            >
              <Plus size={12} />
              新建工作空间
            </Link>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {workspaces.map((ws, index) => (
              <Link
                key={ws.id}
                href={`/workspaces/${ws.id}`}
                className="ct-interactive-card block p-5 rounded-xl border border-outline-variant/30 bg-surface-container-low hover:bg-surface-container transition-colors"
                style={{ animationDelay: `${100 + index * 55}ms` }}
              >
                <div className="flex items-start gap-3">
                  <FolderOpen size={20} className="text-primary shrink-0 mt-0.5" />
                  <div className="min-w-0">
                    <p className="font-medium text-on-surface truncate">{ws.name}</p>
                    <p className="text-xs text-on-surface-variant mt-0.5 truncate">
                      {ws.repo_path}
                    </p>
                    <div className="flex items-center gap-2 mt-2">
                      <span
                        className={`text-xs px-2 py-0.5 rounded-full ${
                          ws.indexed === 1
                            ? "bg-green-400/10 text-green-400"
                            : ws.indexed === -1
                              ? "bg-red-400/10 text-red-400 cursor-help"
                              : "bg-amber-400/10 text-amber-400"
                        }`}
                        title={ws.indexed === -1 && ws.last_index_error ? ws.last_index_error : undefined}
                      >
                        {ws.indexed === 1 ? "已索引" : ws.indexed === -1 ? `索引失败${ws.last_index_error ? " ⓘ" : ""}` : "索引中"}
                      </span>
                      <span className="text-xs text-on-surface-variant">
                        {ws.reports.length} 份报告
                      </span>
                    </div>
                  </div>
                </div>
              </Link>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
