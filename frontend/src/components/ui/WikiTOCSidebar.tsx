"use client";

import { useState } from "react";
import { ChevronRight, ChevronDown, FileText } from "lucide-react";
import type { WikiStructure, WikiSection } from "@/lib/types";

interface WikiTOCSidebarProps {
  structure: WikiStructure;
  currentPageId: string | undefined;
  onPageSelect: (pageId: string) => void;
}

export default function WikiTOCSidebar({
  structure,
  currentPageId,
  onPageSelect,
}: WikiTOCSidebarProps) {
  const [expanded, setExpanded] = useState<Set<string>>(
    () => new Set(structure.rootSections)
  );

  const toggleSection = (sectionId: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(sectionId)) next.delete(sectionId);
      else next.add(sectionId);
      return next;
    });
  };

  const hasSections =
    structure.sections.length > 0 && structure.rootSections.length > 0;

  if (hasSections) {
    return (
      <nav className="space-y-1">
        {structure.rootSections.map((sectionId) => {
          const section = structure.sections.find((s) => s.id === sectionId);
          if (!section) return null;
          return (
            <SectionNode
              key={sectionId}
              section={section}
              allSections={structure.sections}
              pages={structure.pages}
              currentPageId={currentPageId}
              expanded={expanded}
              onToggle={toggleSection}
              onPageSelect={onPageSelect}
              depth={0}
            />
          );
        })}
      </nav>
    );
  }

  // Flat list fallback (no sections)
  return (
    <nav className="space-y-0.5">
      {structure.pages.map((page) => (
        <button
          key={page.id}
          onClick={() => onPageSelect(page.id)}
          className={`w-full text-left px-3 py-2 rounded-lg text-sm transition-colors flex items-center gap-2 ${
            currentPageId === page.id
              ? "bg-primary/15 text-primary font-medium"
              : "text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high/50"
          }`}
        >
          <FileText size={14} className="shrink-0 opacity-50" />
          <span className="truncate">{page.title}</span>
        </button>
      ))}
    </nav>
  );
}

function SectionNode({
  section,
  allSections,
  pages,
  currentPageId,
  expanded,
  onToggle,
  onPageSelect,
  depth,
}: {
  section: WikiSection;
  allSections: WikiSection[];
  pages: WikiStructure["pages"];
  currentPageId: string | undefined;
  expanded: Set<string>;
  onToggle: (id: string) => void;
  onPageSelect: (id: string) => void;
  depth: number;
}) {
  const isExpanded = expanded.has(section.id);
  const Icon = isExpanded ? ChevronDown : ChevronRight;

  return (
    <div>
      <button
        onClick={() => onToggle(section.id)}
        className={`w-full text-left flex items-center gap-1.5 px-2 py-1.5 rounded-lg text-xs font-bold uppercase tracking-wider transition-colors ${
          depth === 0
            ? "text-on-surface-variant/60 hover:text-on-surface"
            : "text-on-surface-variant/50 hover:text-on-surface-variant"
        }`}
        style={{ paddingLeft: `${depth * 12 + 8}px` }}
      >
        <Icon size={12} className="shrink-0" />
        <span className="truncate">{section.title}</span>
      </button>

      {isExpanded && (
        <div className="mt-0.5 space-y-0.5">
          {section.pages.map((pageId) => {
            const page = pages.find((p) => p.id === pageId);
            if (!page) return null;
            return (
              <button
                key={pageId}
                onClick={() => onPageSelect(pageId)}
                className={`w-full text-left py-1.5 rounded-lg text-sm transition-colors flex items-center gap-2 ${
                  currentPageId === pageId
                    ? "bg-primary/15 text-primary font-medium"
                    : "text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high/50"
                }`}
                style={{ paddingLeft: `${(depth + 1) * 12 + 8}px` }}
              >
                <FileText size={13} className="shrink-0 opacity-50" />
                <span className="truncate">{page.title}</span>
              </button>
            );
          })}

          {section.subsections?.map((subId) => {
            const sub = allSections.find((s) => s.id === subId);
            if (!sub) return null;
            return (
              <SectionNode
                key={subId}
                section={sub}
                allSections={allSections}
                pages={pages}
                currentPageId={currentPageId}
                expanded={expanded}
                onToggle={onToggle}
                onPageSelect={onPageSelect}
                depth={depth + 1}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}
