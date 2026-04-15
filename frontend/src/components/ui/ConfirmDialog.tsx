"use client";

import { useEffect, useCallback } from "react";

interface Props {
  open: boolean;
  title: string;
  description: string;
  confirmLabel?: string;
  cancelLabel?: string;
  variant?: "danger" | "warning";
  loading?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export default function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel = "确认",
  cancelLabel = "取消",
  variant = "danger",
  loading = false,
  onConfirm,
  onCancel,
}: Props) {
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "Escape" && !loading) onCancel();
    },
    [onCancel, loading],
  );

  useEffect(() => {
    if (!open) return;
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [open, handleKeyDown]);

  if (!open) return null;

  const confirmColor =
    variant === "danger"
      ? "bg-[#EF4444] hover:bg-[#DC2626] text-white"
      : "bg-[#F59E0B] hover:bg-[#D97706] text-black";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Overlay */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={loading ? undefined : onCancel}
      />
      {/* Dialog */}
      <div className="relative w-full max-w-md mx-4 bg-surface-container-high/95 backdrop-blur-xl border border-outline-variant/10 rounded-2xl p-6 shadow-2xl">
        <h3 className="text-base font-medium text-on-surface mb-2">{title}</h3>
        <p className="text-sm text-on-surface-variant leading-relaxed mb-6">
          {description}
        </p>
        <div className="flex justify-end gap-3">
          <button
            onClick={onCancel}
            disabled={loading}
            className="px-4 py-2 text-sm text-on-surface-variant hover:text-on-surface rounded-lg hover:bg-surface-container-highest/50 transition-colors disabled:opacity-50"
          >
            {cancelLabel}
          </button>
          <button
            onClick={onConfirm}
            disabled={loading}
            className={`px-4 py-2 text-sm font-medium rounded-lg transition-colors disabled:opacity-50 ${confirmColor}`}
          >
            {loading ? "处理中..." : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
