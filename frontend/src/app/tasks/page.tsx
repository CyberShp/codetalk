"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import GlassPanel from "@/components/ui/GlassPanel";
import StatusBadge from "@/components/ui/StatusBadge";
import DataTable from "@/components/ui/DataTable";
import ProgressBar from "@/components/ui/ProgressBar";
import { api } from "@/lib/api";
import type { AnalysisTask } from "@/lib/types";

const tabs = ["all", "running", "completed", "failed", "pending"] as const;
type Filter = (typeof tabs)[number];

export default function TasksPage() {
  const [filter, setFilter] = useState<Filter>("all");
  const [tasks, setTasks] = useState<AnalysisTask[]>([]);

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
      header: "Repository",
      render: (t: AnalysisTask) => (
        <span className="text-on-surface font-data text-xs">
          {t.repository_id.slice(0, 8)}
        </span>
      ),
    },
    {
      key: "type",
      header: "Type",
      render: (t: AnalysisTask) => (
        <span className="text-xs text-on-surface-variant">{t.task_type}</span>
      ),
    },
    {
      key: "tools",
      header: "Tools",
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
      header: "Progress",
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
      header: "Status",
      className: "w-28",
      render: (t: AnalysisTask) => (
        <StatusBadge status={t.status as "running" | "completed" | "failed" | "pending"} />
      ),
    },
    {
      key: "created",
      header: "Created",
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
          Tasks
        </h2>
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
            {tab}
          </button>
        ))}
      </div>

      {/* Task Table */}
      <GlassPanel>
        {tasks.length > 0 ? (
          <DataTable columns={columns} data={tasks} keyField="id" />
        ) : (
          <p className="text-sm text-on-surface-variant/50">
            No tasks found. Create an analysis from the Assets page.
          </p>
        )}
      </GlassPanel>
    </div>
  );
}
