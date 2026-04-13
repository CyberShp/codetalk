import type { ReactNode } from "react";

interface Props {
  children: ReactNode;
  className?: string;
}

export default function GlassPanel({ children, className = "" }: Props) {
  return (
    <div
      className={`bg-surface-container/60 backdrop-blur-xl rounded-lg p-5 ${className}`}
    >
      {children}
    </div>
  );
}
