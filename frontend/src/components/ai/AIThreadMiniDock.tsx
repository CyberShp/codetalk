"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { Bot, Loader2, MessageSquareText, Sparkles } from "lucide-react";
import { api } from "@/lib/api";
import type { AIConversation } from "@/lib/types";

export default function AIThreadMiniDock() {
  const pathname = usePathname();
  const [items, setItems] = useState<AIConversation[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (pathname.startsWith("/ai")) return;
    let cancelled = false;
    let hasLoaded = false;
    const load = async () => {
      if (!hasLoaded) setLoading(true);
      try {
        const result = await api.aiConversations.list({ limit: 3 });
        if (!cancelled) setItems(result.items);
      } catch {
        if (!cancelled) setItems([]);
      } finally {
        hasLoaded = true;
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    const timer = window.setInterval(load, 8000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [pathname]);

  if (pathname.startsWith("/ai")) return null;

  const active = items.find((item) => item.status === "running");
  const target = active ?? items[0];

  if (!target) {
    return (
      <div className="fixed bottom-5 right-5 z-50 hidden md:block">
        <Link href="/ai" className="ct-ai-dock opacity-75">
          <Bot size={17} />
          <span>AI 线程</span>
          {loading ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
        </Link>
      </div>
    );
  }

  return (
    <div className="fixed bottom-5 right-5 z-50">
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
