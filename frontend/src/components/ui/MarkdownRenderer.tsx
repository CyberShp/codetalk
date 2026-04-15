"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { PluggableList } from "unified";
import type { Components } from "react-markdown";
import MermaidRenderer from "./MermaidRenderer";

const components: Components = {
  h1: ({ children }) => (
    <h1 className="font-display text-2xl font-bold text-on-surface mb-4 mt-6">
      {children}
    </h1>
  ),
  h2: ({ children }) => (
    <h2 className="font-display text-xl font-semibold text-on-surface mb-3 mt-5">
      {children}
    </h2>
  ),
  h3: ({ children }) => (
    <h3 className="font-display text-lg font-medium text-on-surface mb-2 mt-4">
      {children}
    </h3>
  ),
  p: ({ children }) => (
    <p className="text-on-surface/90 leading-relaxed mb-3">{children}</p>
  ),
  code: ({ className, children }) => {
    const isBlock = className?.startsWith("language-");
    if (className === "language-mermaid") {
      const chart = String(children).replace(/\n$/, "");
      return <MermaidRenderer chart={chart} />;
    }
    if (isBlock) {
      return (
        <pre className="bg-surface-container-lowest rounded-lg p-4 overflow-x-auto mb-4">
          <code className="font-data text-xs text-on-surface/80">
            {children}
          </code>
        </pre>
      );
    }
    return (
      <code className="font-data text-xs bg-surface-container-high px-1.5 py-0.5 rounded text-primary-fixed-dim">
        {children}
      </code>
    );
  },
  ul: ({ children }) => (
    <ul className="list-disc list-inside text-on-surface/90 mb-3 space-y-1">
      {children}
    </ul>
  ),
  ol: ({ children }) => (
    <ol className="list-decimal list-inside text-on-surface/90 mb-3 space-y-1">
      {children}
    </ol>
  ),
  table: ({ children }) => (
    <div className="overflow-x-auto mb-4">
      <table className="w-full text-sm">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="text-left text-xs text-on-surface-variant/60 uppercase tracking-wider pb-2 pr-4">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="py-1.5 pr-4 text-on-surface/80">{children}</td>
  ),
  blockquote: ({ children }) => (
    <blockquote className="border-l-2 border-primary-container pl-4 text-on-surface-variant italic mb-3">
      {children}
    </blockquote>
  ),
  a: ({ children, href }) => (
    <a
      href={href}
      className="text-primary underline underline-offset-2 hover:text-primary-fixed-dim"
      target="_blank"
      rel="noopener noreferrer"
    >
      {children}
    </a>
  ),
};

export default function MarkdownRenderer({
  content,
  rehypePlugins = [],
}: {
  content: string;
  rehypePlugins?: PluggableList;
}) {
  return (
    <div className="prose-kinetic">
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={rehypePlugins} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  );
}
