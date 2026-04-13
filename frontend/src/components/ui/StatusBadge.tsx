"use client";

type Variant = "running" | "completed" | "failed" | "pending" | "cancelled" | "online" | "offline";

const styles: Record<Variant, { bg: string; glow: string; text: string }> = {
  running: {
    bg: "bg-primary/20",
    glow: "shadow-[0_0_8px_rgba(164,230,255,0.4)]",
    text: "text-primary",
  },
  completed: {
    bg: "bg-secondary/20",
    glow: "shadow-[0_0_8px_rgba(143,214,128,0.4)]",
    text: "text-secondary-fixed-dim",
  },
  failed: {
    bg: "bg-tertiary/20",
    glow: "shadow-[0_0_8px_rgba(255,209,205,0.4)]",
    text: "text-tertiary",
  },
  pending: {
    bg: "bg-on-surface-variant/10",
    glow: "",
    text: "text-on-surface-variant",
  },
  cancelled: {
    bg: "bg-on-surface-variant/10",
    glow: "",
    text: "text-on-surface-variant",
  },
  online: {
    bg: "bg-secondary/20",
    glow: "shadow-[0_0_6px_rgba(236,255,227,0.4)]",
    text: "text-secondary-fixed-dim",
  },
  offline: {
    bg: "bg-tertiary/20",
    glow: "shadow-[0_0_6px_rgba(255,209,205,0.3)]",
    text: "text-tertiary",
  },
};

export default function StatusBadge({ status }: { status: Variant }) {
  const s = styles[status] ?? styles.pending;
  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium ${s.bg} ${s.text} ${s.glow}`}
    >
      <span
        className={`w-1.5 h-1.5 rounded-full ${s.text.replace("text-", "bg-")}`}
      />
      {status}
    </span>
  );
}
