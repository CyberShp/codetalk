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
  return (
    <div
      className={`bg-surface-container/60 backdrop-blur-xl rounded-lg p-5 flex flex-col gap-3 ${
        comingSoon ? "opacity-50" : ""
      }`}
    >
      <div className="flex items-start justify-between">
        <div>
          <h3 className="font-display font-semibold text-on-surface">
            {name}
          </h3>
          <p className="text-xs text-on-surface-variant mt-1">{description}</p>
        </div>
        <StatusBadge status={comingSoon ? "offline" : healthy ? "online" : "offline"} />
      </div>
      <div className="flex flex-wrap gap-1.5 mt-auto">
        {capabilities.map((cap) => (
          <span
            key={cap}
            className="text-[10px] px-2 py-0.5 rounded-full bg-primary/10 text-primary-fixed-dim"
          >
            {cap}
          </span>
        ))}
      </div>
      {comingSoon && (
        <span className="text-xs text-on-surface-variant/50 italic">
          Coming Soon
        </span>
      )}
    </div>
  );
}
