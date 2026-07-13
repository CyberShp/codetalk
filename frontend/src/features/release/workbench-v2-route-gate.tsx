"use client";

import { AlertTriangle, Loader2, RefreshCw } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { workbenchReleaseApi } from "@/lib/api/workbench-release";

type GateState = "loading" | "enabled" | "error";

export function WorkbenchV2RouteGate({
  children,
  legacyDestination,
}: {
  children: ReactNode;
  legacyDestination: string;
}) {
  const router = useRouter();
  const [state, setState] = useState<GateState>("loading");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    void workbenchReleaseApi.get().then((release) => {
      if (!active) return;
      if (!release.workbench_v2_enabled) {
        router.replace(legacyDestination);
        return;
      }
      setState("enabled");
    }).catch(() => {
      if (active) setState("error");
    });
    return () => {
      active = false;
    };
  }, [attempt, legacyDestination, router]);

  if (state === "enabled") return children;
  if (state === "error") {
    return (
      <section className="ct-v2-empty-state is-error" role="alert">
        <AlertTriangle size={24} />
        <h1>无法确认 Workbench 版本</h1>
        <p>后端发布状态暂时不可用，请确认 API 服务已启动。</p>
        <button type="button" onClick={() => { setState("loading"); setAttempt((value) => value + 1); }}>
          <RefreshCw size={14} />重试
        </button>
      </section>
    );
  }
  return <div className="ct-v2-page-loading" role="status"><Loader2 className="animate-spin" />正在确认 Workbench 版本…</div>;
}
