import { Suspense } from "react";
import { TaskCenterPage } from "@/features/tasks/task-center-page";

export default function TasksPage() {
  return <Suspense fallback={<div className="ct-v2-page-loading">正在读取任务…</div>}><TaskCenterPage /></Suspense>;
}
