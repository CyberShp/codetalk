import type { CSSProperties, ReactNode } from "react";

interface Props {
  children: ReactNode;
  className?: string;
  style?: CSSProperties;
}

export default function GlassPanel({ children, className = "", style }: Props) {
  return (
    <div
      className={`bg-surface-container/60 backdrop-blur-xl rounded-lg p-5 ${className}`}
      style={style}
    >
      {children}
    </div>
  );
}
