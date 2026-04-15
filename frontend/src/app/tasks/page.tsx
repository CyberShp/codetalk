"use client";

import { Suspense, useState, useEffect, useCallback } from "react";
import Link from "next/link";
import { useSearchParams, useRouter } from "next/navigation";
import GlassPanel from "@/components/ui/GlassPanel";
import StatusBadge from "@/components/ui/StatusBadge";
import DataTable from "@/components/ui/DataTable";
import ProgressBar from "@/components/ui/ProgressBar";
import NewAnalysisModal from "@/components/ui/NewAnalysisModal";
import { api } from "@/lib/api";
import type { AnalysisTask } from "@/lib/types";

const tabs = ["all", "running", "completed", "failed", "pending"] as const;
type Filter = (typeof tabs)[number];

export default function TasksPage() {
  return (
    <Suspense>
      <TasksPageInner />
    </Suspense>
  );
}

function TasksPageInner() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const [filter, setFilter] = useState<Filter>("all");
  const [tasks, setTasks] = useState<AnalysisTask[]>([]);
  const [showNewAnalysis, setShowNewAnalysis] = useState(
    searchParams.get("new") === "true",
  );

  const loadTasks = useCallback(async () => {
    try {
      const params = filter === "all" ? undefined : { status: filter };
      const data = await api.tasks.list(params);
      setTasks(data);
    } catch (e) {
      console.error("Failed to load tasks:", e);
    }
  }, [filter]);

  useEffect(() => {
    loadTasks();
    const interval = setInterval(loadTasks, 5000);
    return () => clearInterval(interval);
  }, [loadTasks]);

  const columns = [
    {
      key: "id",
      header: "ID",
      className: "w-24",
      render: (t: AnalysisTask) => (
        <Link
          href={`/tasks/${t.id}`}
          className="font-data text-xs text-primary hover:underline"
        >
          {t.id.slice(0, 8)}
        </Link>
      ),
    },
    {
      key: "repo",
      header: "仓库",
      render: (t: AnalysisTask) => (
        <span className="text-on-surface font-data text-xs">
          {t.repository_id.slice(0, 8)}
        </span>
      ),
    },
    {
      key: "type",
      header: "类型",
      render: (t: AnalysisTask) => (
        <span className="text-xs text-on-surface-variant">{t.task_type}</span>
      ),
    },
    {
      key: "tools",
      header: "工具",
      render: (t: AnalysisTask) => (
        <div className="flex gap-1">
          {t.tools.map((tool) => (
            <span
              key={tool}
              className="text-[10px] px-2 py-0.5 rounded-full bg-primary/10 text-primary-fixed-dim"
            >
              {tool}
            </span>
          ))}
        </div>
      ),
    },
    {
      key: "progress",
      header: "进度",
      className: "w-36",
      render: (t: AnalysisTask) => (
        <div className="flex items-center gap-2">
          <ProgressBar value={t.progress} className="flex-1" />
          <span className="font-data text-xs text-on-surface-variant w-8 text-right">
            {t.progress}%
          </span>
        </div>
      ),
    },
    {
      key: "status",
      header: "状态",
      className: "w-28",
      render: (t: AnalysisTask) => (
        <StatusBadge status={t.status as "running" | "completed" | "failed" | "pending"} />
      ),
    },
    {
      key: "created",
      header: "创建时间",
      render: (t: AnalysisTask) => (
        <span className="text-xs text-on-surface-variant font-data">
          {new Date(t.created_at).toLocaleDateString()}
        </span>
      ),
    },
  ];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="font-display text-lg font-semibold text-on-surface">
          任务
        </h2>
        <button
          onClick={() => setShowNewAnalysis(true)}
          className="px-4 py-2 text-sm font-medium rounded-md bg-primary-container text-primary hover:shadow-[0_0_12px_rgba(164,230,255,0.2)] transition-shadow"
        >
          新建分析
        </button>
      </div>

      {/* Filter Tabs */}
      <div className="flex gap-1 bg-surface-container-low rounded-lg p-1 w-fit">
        {tabs.map((tab) => (
          <button
            key={tab}
            onClick={() => setFilter(tab)}
            className={`px-4 py-1.5 text-xs rounded-md capitalize transition-colors ${
              filter === tab
                ? "bg-surface-container-high text-on-surface"
                : "text-on-surface-variant hover:text-on-surface"
            }`}
          >
            {{ all: "全部", running: "运行中", completed: "已完成", failed: "失败", pending: "等待中" }[tab]}
          </button>
        ))}
      </div>

      {/* Task Table */}
      <GlassPanel>
        {tasks.length > 0 ? (
          <DataTable columns={columns} data={tasks} keyField="id" />
        ) : (
          <p className="text-sm text-on-surface-variant/50">
            暂无任务。前往资产页面创建分析。
          </p>
        )}
      </GlassPanel>

      {/* New Analysis Modal */}
      {showNewAnalysis && (
        <NewAnalysisModal
          onClose={() => {
            setShowNewAnalysis(false);
            router.replace("/tasks", { scroll: false });
          }}
        />
      )}
    </div>
  );
}
