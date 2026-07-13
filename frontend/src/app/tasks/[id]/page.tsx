"use client";

import { useParams } from "next/navigation";
import LegacyTaskDetailPage from "@/features/tasks/legacy-task-detail-page";
import { WorkbenchTaskDetailPage } from "@/features/tasks/workbench-task-detail-page";

export default function TaskDetailRoute() {
  const { id } = useParams<{ id: string }>();
  return id.startsWith("task_")
    ? <WorkbenchTaskDetailPage taskId={id} />
    : <LegacyTaskDetailPage />;
}
