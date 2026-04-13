"use client";

import type { InputHTMLAttributes } from "react";

interface Props extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
}

export default function CyberInput({ label, className = "", ...rest }: Props) {
  return (
    <label className="block">
      {label && (
        <span className="block text-xs text-on-surface-variant mb-1.5 tracking-wide uppercase">
          {label}
        </span>
      )}
      <input
        className={`w-full bg-surface-container-lowest/50 text-on-surface font-data text-sm px-4 py-2 rounded-md outline-none placeholder:text-on-surface-variant/40 focus:ring-1 focus:ring-primary-container focus:shadow-[inset_0_0_8px_rgba(164,230,255,0.08)] transition-shadow ${className}`}
        {...rest}
      />
    </label>
  );
}
