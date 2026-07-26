export const taskLifecycleLabels: Record<string, string> = {
  draft: "草稿", ready: "就绪", archived: "已归档",
};

export const taskExecutionLabels: Record<string, string> = {
  prepared: "已准备", queued: "排队中", running: "运行中", completed: "已完成",
  partial: "部分完成",
  failed: "失败", error: "失败", cancelled: "已取消", interrupted: "已中断", quality_blocked: "已阻断",
  blocked: "已阻断", not_started: "未运行",
};

export const taskQualityLabels: Record<string, string> = {
  not_checked: "未检查", pending: "检查中", passed: "通过", warning: "有警告", blocked: "已阻断",
  not_evaluated: "未检查", failed: "已阻断",
};

export const taskDeliveryLabels: Record<string, string> = {
  none: "暂无交付", partial: "部分交付", complete: "交付完整",
  pending: "暂无交付", ready: "交付完整", failed: "部分交付",
};

export function taskStatusLabel(labels: Record<string, string>, value: string) {
  return labels[value] || value || "—";
}
