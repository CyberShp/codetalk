"use client";

import { useEffect, useRef, useState } from "react";

export default function MermaidRenderer({ chart }: { chart: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [svg, setSvg] = useState<string>("");
  const [error, setError] = useState<string>("");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const mermaid = (await import("mermaid")).default;
        mermaid.initialize({
          startOnLoad: false,
          theme: "dark",
          themeVariables: {
            darkMode: true,
            background: "#10141A",
            primaryColor: "#00687F",
            primaryTextColor: "#DFE2EB",
            primaryBorderColor: "#44474E",
            lineColor: "#BFC5D0",
            secondaryColor: "#262A31",
            tertiaryColor: "#1C2026",
          },
        });
        const id = `mermaid-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
        const { svg: rendered } = await mermaid.render(id, chart);
        if (!cancelled) {
          setSvg(rendered);
          setError("");
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "Mermaid render failed");
          setSvg("");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [chart]);

  if (error) {
    return (
      <div className="bg-surface-container-lowest rounded-lg p-4">
        <p className="text-xs text-tertiary font-data">
          Diagram render error: {error}
        </p>
        <pre className="text-xs text-on-surface-variant/50 mt-2 font-data">
          {chart}
        </pre>
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className="bg-surface-container-lowest rounded-lg p-4 overflow-x-auto [&_svg]:max-w-full"
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}
