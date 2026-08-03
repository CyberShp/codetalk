import { request } from "@/lib/api";
import type { QualityEvaluationReport, QualityEvaluationScope } from "@/lib/types";

export const qualityEvaluationsApi = {
  get: (taskRunId: string, scope?: QualityEvaluationScope, signal?: AbortSignal) => {
    const query = scope ? `?scope=${encodeURIComponent(scope)}` : "";
    return request<QualityEvaluationReport>(
      `/api/workbench/task-runs/${encodeURIComponent(taskRunId)}/quality-evaluation${query}`,
      { signal },
    );
  },
};
