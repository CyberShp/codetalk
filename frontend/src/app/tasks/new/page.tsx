import { Suspense } from "react";
import { TaskWizard } from "@/features/tasks/task-wizard";

export default function NewTaskPage() {
  return <Suspense fallback={<div className="ct-v2-page-loading">正在准备任务向导…</div>}><TaskWizard /></Suspense>;
}
