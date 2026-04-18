"use client";

import { useState, useEffect } from "react";
import GlassPanel from "./GlassPanel";
import ChatPanel from "./ChatPanel";
import { useChatEngine } from "@/hooks/useChatEngine";
import { MessageSquare, X } from "lucide-react";

interface Props {
  repoId: string;
  /** File paths from the currently viewed wiki page. Undefined = global context. */
  currentPageFilePaths?: string[];
  /** When true, hide the floating bubble (used when docked chat is active). */
  hidden?: boolean;
}

export default function FloatingChat({ repoId, currentPageFilePaths, hidden }: Props) {
  const engine = useChatEngine({ repoId, currentPageFilePaths });
  const [isOpen, setIsOpen] = useState(false);

  useEffect(() => {
    if (isOpen && engine.inputRef.current) {
      engine.inputRef.current.focus();
    }
  }, [isOpen, engine.inputRef]);

  return (
    <div className={`fixed bottom-6 right-6 z-50 flex flex-col items-end${hidden ? " hidden" : ""}`}>
      {isOpen && (
        <div
          className="mb-4 w-80 sm:w-96 animate-in fade-in slide-in-from-bottom-6 duration-500 ease-out rounded-t-2xl overflow-hidden outline outline-1 outline-white/10"
          style={{ height: "min(500px, calc(100vh - 7rem))" }}
        >
          <GlassPanel className="h-full flex flex-col overflow-hidden shadow-[0_-20px_80px_-20px_rgba(0,0,0,0.8)] border-none bg-[#0D0D0F]/90 backdrop-blur-2xl">
            {/* Cyber Cap Header */}
            <div className="relative h-11 shrink-0 flex items-center justify-between px-4 bg-black/40 border-b border-white/5">
              <div className="absolute top-0 left-0 w-full h-[1px] bg-gradient-to-r from-transparent via-primary/50 to-transparent" />
              <div className="flex items-center gap-2">
                <div className="w-1.5 h-1.5 rounded-full bg-primary shadow-lg shadow-primary/60 animate-pulse" />
                <h3 className="text-[10px] font-mono font-bold uppercase tracking-[0.2em] text-on-surface/70">
                  Neural Link <span className="text-primary/50">v2.5</span>
                </h3>
              </div>
              <div className="flex items-center gap-3">
                <span className="text-[9px] font-mono text-on-surface-variant/40 hidden sm:block italic">
                  {currentPageFilePaths?.length
                    ? `${currentPageFilePaths.length} files in scope`
                    : "GLOBAL_CONTEXT"}
                </span>
                <button
                  onClick={() => setIsOpen(false)}
                  className="p-1 rounded-md text-on-surface-variant hover:text-on-surface hover:bg-white/5 transition-all"
                >
                  <X size={14} />
                </button>
              </div>
            </div>

            <ChatPanel engine={engine} repoId={repoId} className="flex-1 min-h-0" />
          </GlassPanel>
        </div>
      )}

      {/* Floating action button */}
      <button
        onClick={() => setIsOpen(!isOpen)}
        className={`w-14 h-14 rounded-full flex items-center justify-center shadow-lg transition-all duration-300 ${
          isOpen
            ? "bg-on-surface text-surface rotate-90 scale-90"
            : "bg-primary text-on-primary hover:scale-110 hover:shadow-primary/20"
        }`}
      >
        {isOpen ? <X size={24} /> : <MessageSquare size={24} />}
        {!isOpen && (
          <span className="absolute -top-1 -right-1 w-4 h-4 bg-secondary rounded-full border-2 border-surface animate-bounce" />
        )}
      </button>
    </div>
  );
}
