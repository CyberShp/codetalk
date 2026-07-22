import { request } from "@/lib/api";
import type { WorkbenchRunSummary, WorkbenchTask, WorkbenchTaskListQuery } from "@/lib/types/task";

const taskPath = (taskId: string) => `/api/workbench/tasks/${encodeURIComponent(taskId)}`;

export const workbenchTasksApi = {
  list: (query: WorkbenchTaskListQuery = {}) => {
    const params = new URLSearchParams();
    Object.entries(query).forEach(([key, value]) => {
      if (value !== undefined && value !== "") params.set(key, String(value));
    });
    const suffix = params.size ? `?${params.toString()}` : "";
    return request<{ items: WorkbenchTask[]; total: number; page: number; page_size: number }>(`/api/workbench/tasks${suffix}`);
  },
  get: (taskId: string) => request<WorkbenchTask>(taskPath(taskId)),
  create: (payload: Record<string, unknown>) => request<WorkbenchTask>("/api/workbench/tasks", { method: "POST", body: JSON.stringify(payload) }),
  update: (taskId: string, changes: Partial<Pick<WorkbenchTask, "name" | "description" | "lifecycle_status" | "input_values" | "execution_overrides" | "output_overrides" | "tags">>) => request<WorkbenchTask>(taskPath(taskId), { method: "PATCH", body: JSON.stringify(changes) }),
  archive: (taskId: string) => request<WorkbenchTask>(`${taskPath(taskId)}/archive`, { method: "POST" }),
  clone: (taskId: string, name?: string) => request<WorkbenchTask>(`${taskPath(taskId)}/clone`, { method: "POST", body: JSON.stringify({ name: name || null }) }),
  createRun: (taskId: string, parentTaskRunId = "", executionProfileId = "") => request<WorkbenchRunSummary>(`${taskPath(taskId)}/runs`, { method: "POST", body: JSON.stringify({ parent_task_run_id: parentTaskRunId, execution_profile_id: executionProfileId }) }),
  compile: (taskId: string) => request<{ compiled_definition: Record<string, unknown>; compiled_plan: Record<string, unknown>; validation: { valid: boolean; errors: Array<{ message: string }>; warnings: Array<{ message: string }> } }>(`${taskPath(taskId)}/compile`, { method: "POST" }),
  history: () => request<{ items: WorkbenchRunSummary[] }>("/api/workbench/tasks/history/runs"),
};
