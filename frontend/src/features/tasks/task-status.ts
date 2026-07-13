export const taskLifecycleLabels: Record<string, string> = {
  draft: "草稿", ready: "就绪", archived: "已归档",
};

export const taskExecutionLabels: Record<string, string> = {
  prepared: "已准备", queued: "排队中", running: "运行中", completed: "已完成",
  failed: "失败", error: "失败", cancelled: "已取消", interrupted: "已中断",
  blocked: "已阻断", not_started: "未运行",
};

export const taskQualityLabels: Record<string, string> = {
  not_evaluated: "未评估", passed: "通过", failed: "未通过", warning: "有警告",
};

export const taskDeliveryLabels: Record<string, string> = {
  pending: "待交付", ready: "可交付", partial: "部分交付", failed: "交付失败",
};

export function taskStatusLabel(labels: Record<string, string>, value: string) {
  return labels[value] || value || "—";
}
