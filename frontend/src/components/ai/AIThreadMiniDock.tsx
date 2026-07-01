"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { Bot, Loader2, MessageSquareText, Sparkles } from "lucide-react";
import { api } from "@/lib/api";
import type { AIConversation } from "@/lib/types";

const ACTIVE_THREAD_POLL_MS = 8000;
const IDLE_THREAD_POLL_MS = 60000;

export default function AIThreadMiniDock() {
  const pathname = usePathname();
  const [items, setItems] = useState<AIConversation[]>([]);
  const [loading, setLoading] = useState(false);
  const mountedRef = useRef(true);

  const loadThreads = useCallback(async (options: { showSpinner?: boolean } = {}) => {
    if (document.hidden) return;
    if (options.showSpinner) setLoading(true);
    try {
      const result = await api.aiConversations.list({ limit: 3 });
      if (mountedRef.current) setItems(result.items);
    } catch {
      if (mountedRef.current) setItems((current) => (current.length ? current : []));
    } finally {
      if (mountedRef.current && options.showSpinner) setLoading(false);
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (pathname.startsWith("/ai")) return;
    void loadThreads({ showSpinner: true });
  }, [loadThreads, pathname]);

  const hasRunningThread = items.some((item) => item.status === "running");
  const pollDelay = hasRunningThread ? ACTIVE_THREAD_POLL_MS : IDLE_THREAD_POLL_MS;

  useEffect(() => {
    if (pathname.startsWith("/ai")) return;
    const timer = window.setInterval(() => {
      void loadThreads();
    }, pollDelay);
    return () => {
      window.clearInterval(timer);
    };
  }, [loadThreads, pathname, pollDelay]);

  useEffect(() => {
    if (pathname.startsWith("/ai")) return;
    const handleVisibilityChange = () => {
      if (!document.hidden) void loadThreads();
    };
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => document.removeEventListener("visibilitychange", handleVisibilityChange);
  }, [loadThreads, pathname]);

  if (pathname.startsWith("/ai")) return null;

  const active = items.find((item) => item.status === "running");
  const target = active ?? items[0];
  const dockWrapperClass = "ct-ai-dock-wrap fixed bottom-5 right-5 z-30";

  if (!target) {
    return (
      <div className={`${dockWrapperClass} hidden md:block`}>
        <Link href="/ai" className="ct-ai-dock opacity-75">
          <Bot size={17} />
          <span>AI 线程</span>
          {loading ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
        </Link>
      </div>
    );
  }

  return (
    <div className={dockWrapperClass}>
      <Link
        href={`/ai/${target.id}`}
        className="ct-ai-dock group"
        title={target.status === "running" ? "AI 正在生成，点击打开线程" : "打开最近 AI 线程"}
      >
        <span className="ct-ai-dock__orb">
          {target.status === "running" ? (
            <Loader2 size={16} className="animate-spin" />
          ) : (
            <MessageSquareText size={16} />
          )}
        </span>
        <span className="min-w-0">
          <span className="block max-w-[190px] truncate text-[12px] font-semibold">
            {target.title}
          </span>
          <span className="block text-[10px] text-on-surface-variant">
            {target.status === "running" ? "生成中，页面切换不会中断" : "继续调查线程"}
          </span>
        </span>
      </Link>
    </div>
  );
}
