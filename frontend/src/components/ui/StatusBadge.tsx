"use client";

type Variant = "running" | "completed" | "failed" | "pending" | "cancelled" | "online" | "offline";

const styles: Record<Variant, { bg: string; border: string; dot: string; text: string; animate?: string; glow: string }> = {
  running: {
    bg: "bg-primary/10",
    border: "border-primary/20",
    dot: "bg-primary",
    text: "text-primary",
    animate: "animate-pulse",
    glow: "shadow-[0_0_8px_rgba(164,230,255,0.3)]",
  },
  completed: {
    bg: "bg-secondary/10",
    border: "border-secondary/20",
    dot: "bg-secondary-fixed-dim",
    text: "text-secondary-fixed-dim",
    glow: "shadow-none",
  },
  failed: {
    bg: "bg-tertiary/10",
    border: "border-tertiary/20",
    dot: "bg-tertiary",
    text: "text-tertiary",
    glow: "shadow-none",
  },
  pending: {
    bg: "bg-on-surface-variant/5",
    border: "border-outline-variant/30",
    dot: "bg-on-surface-variant/50",
    text: "text-on-surface-variant",
    glow: "shadow-none",
  },
  cancelled: {
    bg: "bg-on-surface-variant/5",
    border: "border-outline-variant/30",
    dot: "bg-on-surface-variant/50",
    text: "text-on-surface-variant",
    glow: "shadow-none",
  },
  online: {
    bg: "bg-secondary/10",
    border: "border-secondary/30",
    dot: "bg-secondary-fixed-dim",
    text: "text-secondary-fixed-dim",
    animate: "animate-pulse",
    glow: "shadow-[0_0_8px_rgba(143,214,128,0.3)]",
  },
  offline: {
    bg: "bg-tertiary/10",
    border: "border-tertiary/20",
    dot: "bg-tertiary/60",
    text: "text-tertiary",
    glow: "shadow-none",
  },
};

const labels: Record<Variant, string> = {
  running: "运行中",
  completed: "已完成",
  failed: "失败",
  pending: "等待中",
  cancelled: "已取消",
  online: "在线",
  offline: "离线",
};

export default function StatusBadge({ status }: { status: Variant }) {
  const s = styles[status] ?? styles.pending;
  
  return (
    <span
      className={`inline-flex items-center gap-2 px-2.5 py-0.5 rounded-full text-[11px] font-semibold border backdrop-blur-md transition-all duration-300 ${s.bg} ${s.border} ${s.text} ${s.glow}`}
    >
      <span className="relative flex h-1.5 w-1.5">
        {s.animate && (
          <span className={`absolute inline-flex h-full w-full rounded-full opacity-75 animate-ping ${s.dot}`} />
        )}
        <span className={`relative inline-flex rounded-full h-1.5 w-1.5 ${s.dot}`} />
      </span>
      <span className="leading-tight">
        {labels[status] ?? status}
      </span>
    </span>
  );
}
