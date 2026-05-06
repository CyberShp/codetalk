"use client";

import { useEffect } from "react";

/**
 * Fires `refresh` only when the page is restored from the browser's
 * back-forward cache (BFCache). Deliberately does NOT fire on ordinary
 * tab-switch (visibilitychange) to avoid hammering expensive health-check
 * endpoints every time the user briefly leaves and returns.
 */
export function usePageRestoreRefresh(refresh: () => void) {
  useEffect(() => {
    const handlePageShow = (e: PageTransitionEvent) => {
      if (e.persisted) {
        refresh();
      }
    };

    window.addEventListener("pageshow", handlePageShow);
    return () => {
      window.removeEventListener("pageshow", handlePageShow);
    };
  }, [refresh]);
}
