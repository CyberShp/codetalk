import { request } from "@/lib/api";

export interface WorkbenchReleaseStatus {
  workbench_v2_enabled: boolean;
}

export const workbenchReleaseApi = {
  get: () => request<WorkbenchReleaseStatus>("/api/workbench/release"),
};
