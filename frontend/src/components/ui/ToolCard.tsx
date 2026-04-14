import StatusBadge from "./StatusBadge";

interface Props {
  name: string;
  description: string;
  capabilities: string[];
  healthy: boolean;
  comingSoon?: boolean;
}

export default function ToolCard({
  name,
  description,
  capabilities,
  healthy,
  comingSoon,
}: Props) {
  const status = comingSoon ? "offline" : healthy ? "online" : "offline";

  return (
    <div
      className={`relative group bg-surface-container/40 backdrop-blur-xl border border-outline-variant/20 rounded-xl p-5 flex flex-col gap-4 transition-all duration-300 hover:bg-surface-container/60 hover:border-primary/30 hover:shadow-lg ${
        comingSoon ? "grayscale-[0.5] opacity-70" : ""
      }`}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0">
          <h3 className="font-display font-bold text-on-surface text-base truncate group-hover:text-primary transition-colors">
            {name}
          </h3>
          <p className="text-xs text-on-surface-variant mt-1.5 leading-relaxed line-clamp-2 h-8">
            {description}
          </p>
        </div>
        <div className="shrink-0 pt-0.5">
          <StatusBadge status={status} />
        </div>
      </div>

      <div className="flex flex-wrap gap-1.5 mt-auto pt-2">
        {capabilities.map((cap) => (
          <span
            key={cap}
            className="text-[10px] px-2 py-0.5 rounded-md bg-primary/5 text-primary-fixed-dim border border-primary/10 font-medium"
          >
            {cap}
          </span>
        ))}
      </div>

      {comingSoon && (
        <div className="absolute inset-0 flex items-center justify-center bg-surface-container/20 rounded-xl pointer-events-none">
          <span className="bg-surface-container-high/90 px-3 py-1 rounded-full text-[10px] font-bold text-on-surface-variant border border-outline-variant/30 shadow-sm">
            即将推出
          </span>
        </div>
      )}
    </div>
  );
}
