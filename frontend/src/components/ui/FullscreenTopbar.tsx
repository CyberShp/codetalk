"use client";

import Link from "next/link";
import { ChevronRight } from "lucide-react";
import type { ReactNode } from "react";

interface FullscreenTopbarProps {
  /** Repo name shown in the breadcrumb after "CodeTalks" */
  repoName?: string;
  /** Link target for the repo breadcrumb item */
  repoHref?: string;
  /** Current-page label shown after the repo breadcrumb (not a link) */
  pageTitle?: string;
  /** Optional content rendered on the right side of the bar */
  actions?: ReactNode;
}

/**
 * Shared fixed topbar for all fullscreen pages (wiki, graph, chat, repo main).
 *
 * Layout:  CodeTalks  >  <repoName>  >  <pageTitle>        [actions]
 */
export default function FullscreenTopbar({
  repoName,
  repoHref,
  pageTitle,
  actions,
}: FullscreenTopbarProps) {
  return (
    <header className="fixed top-0 left-0 right-0 z-50 h-12 flex items-center gap-1.5 px-4
      bg-[#050506]/80 backdrop-blur-[12px] border-b border-white/[0.06]">

      {/* Brand */}
      <Link
        href="/"
        className="text-sm font-semibold tracking-wide text-[#A4E6FF]/90 hover:text-[#A4E6FF]
          transition-colors whitespace-nowrap"
      >
        CodeTalks
      </Link>

      {/* Repo breadcrumb */}
      {repoName && (
        <>
          <ChevronRight size={13} className="text-white/25 flex-shrink-0" />
          {repoHref ? (
            <Link
              href={repoHref}
              className="text-sm text-white/60 hover:text-white/90 transition-colors
                truncate max-w-[180px]"
              title={repoName}
            >
              {repoName}
            </Link>
          ) : (
            <span className="text-sm text-white/60 truncate max-w-[180px]" title={repoName}>
              {repoName}
            </span>
          )}
        </>
      )}

      {/* Page section label */}
      {pageTitle && (
        <>
          <ChevronRight size={13} className="text-white/25 flex-shrink-0" />
          <span className="text-sm text-white/40 truncate max-w-[160px]" title={pageTitle}>
            {pageTitle}
          </span>
        </>
      )}

      <div className="flex-1" />

      {/* Right-side action slot */}
      {actions && (
        <div className="flex items-center gap-2">
          {actions}
        </div>
      )}
    </header>
  );
}
