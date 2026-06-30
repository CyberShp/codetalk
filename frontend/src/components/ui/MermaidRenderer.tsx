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
          theme: "base",
          suppressErrorRendering: true,
          themeVariables: {
            background: "#FFFFFF",
            mainBkg: "#FFFFFF",
            primaryColor: "#E7F6FA",
            primaryTextColor: "#172026",
            primaryBorderColor: "#77AFC0",
            lineColor: "#52636D",
            secondaryColor: "#F3F8FA",
            tertiaryColor: "#F7FAFC",
            clusterBkg: "#F8FBFC",
            clusterBorder: "#C8D8DE",
            edgeLabelBackground: "#FFFFFF",
            textColor: "#172026",
            noteBkgColor: "#FFF8E8",
            noteTextColor: "#172026",
            noteBorderColor: "#E5C56F",
          },
        });
        const parsed = await mermaid.parse(chart, { suppressErrors: true });
        if (!parsed) {
          throw new Error("Invalid Mermaid diagram syntax");
        }
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
