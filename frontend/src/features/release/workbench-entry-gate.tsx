"use client";

import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { AlertTriangle, Loader2, RefreshCw } from "lucide-react";

import { workbenchReleaseApi } from "@/lib/api/workbench-release";

type LegacyView = "run" | "workflow" | "knowledge";
type GateState = "loading" | "legacy" | "error";

const LegacyWorkbench = dynamic(
  () => import("@/app/workbench/agent-workbench-experience").then(
    (module) => module.AgentWorkbenchExperience,
  ),
  { ssr: false },
);

export function WorkbenchEntryGate({
  destination,
  legacyView,
}: {
  destination: string;
  legacyView: LegacyView;
}) {
  const router = useRouter();
  const [state, setState] = useState<GateState>("loading");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    void workbenchReleaseApi.get().then((release) => {
      if (!active) return;
      if (release.workbench_v2_enabled) {
        router.replace(destination);
        return;
      }
      setState("legacy");
    }).catch(() => {
      if (active) setState("error");
    });
    return () => {
      active = false;
    };
  }, [attempt, destination, router]);

  const retry = () => {
    setState("loading");
    setAttempt((value) => value + 1);
  };

  if (state === "legacy") {
    return <LegacyWorkbench initialView={legacyView} />;
  }

  if (state === "error") {
    return (
      <section className="mx-auto mt-16 max-w-lg rounded-lg border border-error/30 bg-error-container/30 p-5" role="alert">
        <div className="flex items-start gap-3">
          <AlertTriangle className="mt-0.5 shrink-0 text-error" size={20} aria-hidden />
          <div className="min-w-0">
            <h1 className="text-base font-semibold text-on-surface">无法确认 Workbench 版本</h1>
            <p className="mt-1 text-sm leading-6 text-on-surface-variant">
              后端发布状态暂时不可用。请确认 API 服务已启动后重试。
            </p>
            <button type="button" onClick={retry} className="mt-4 inline-flex items-center gap-2 rounded-md bg-primary px-3 py-2 text-sm font-medium text-on-primary">
              <RefreshCw size={16} aria-hidden />
              重试
            </button>
          </div>
        </div>
      </section>
    );
  }

  return (
    <div className="flex min-h-[50vh] items-center justify-center gap-2 text-sm text-on-surface-variant" role="status">
      <Loader2 size={18} className="animate-spin" aria-hidden />
      正在进入 Workbench…
    </div>
  );
}
