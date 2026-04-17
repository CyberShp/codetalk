"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ArrowLeft } from "lucide-react";

import InsightAskPanel from "@/components/ui/InsightAskPanel";
import { api } from "@/lib/api";
import type { TaskDetail } from "@/lib/types";

export default function TaskAskPage() {
  const params = useParams();
  const taskId = params.id as string;
  const [task, setTask] = useState<TaskDetail | null>(null);

  useEffect(() => {
    let alive = true;

    async function loadTask() {
      try {
        const detail = await api.tasks.get(taskId);
        if (alive) {
          setTask(detail);
        }
      } catch {
        if (alive) {
          setTask(null);
        }
      }
    }

    if (taskId) {
      loadTask();
    }

    return () => {
      alive = false;
    };
  }, [taskId]);

  const repoName = task?.repository_name ?? "Repository";

  return (
    <div className="fixed inset-0 z-[90] bg-surface text-on-surface">
      <header className="fixed inset-x-0 top-0 z-[95] flex h-16 items-center justify-between border-b border-outline-variant/20 bg-surface/95 px-6 backdrop-blur-md">
        <div className="flex min-w-0 items-center gap-3">
          <Link
            href={`/tasks/${taskId}`}
            className="inline-flex h-10 items-center gap-2 rounded-full border border-outline-variant/20 bg-surface-container-low px-4 text-sm font-medium text-on-surface transition-colors hover:border-primary/30 hover:text-primary"
          >
            <ArrowLeft size={16} />
            返回任务
          </Link>
          <div className="h-6 w-px bg-outline-variant/20" />
          <Link
            href="/dashboard"
            className="text-[11px] font-black uppercase tracking-[0.35em] text-on-surface hover:text-primary"
          >
            CODETALKS
          </Link>
          <span className="truncate text-sm text-on-surface-variant">{repoName}</span>
        </div>
      </header>

      <div className="absolute inset-x-0 bottom-0 top-16">
        <InsightAskPanel
          taskId={taskId}
          repoId={task?.repository_id}
          className="h-full"
        />
      </div>
    </div>
  );
}
