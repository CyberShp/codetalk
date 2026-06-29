interface Props {
  value: number;
  className?: string;
}

export default function ProgressBar({ value, className = "" }: Props) {
  const pct = Math.max(0, Math.min(100, value));
  return (
    <div
      className={`h-1.5 w-full rounded-full bg-surface-container-high overflow-hidden ${className}`}
    >
      <div
        className="ct-progress-fill h-full rounded-full transition-all duration-500"
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}
