"use client";

import { useParams } from "next/navigation";
import { RunCockpitPage } from "@/features/runs/run-cockpit-page";

export default function TaskRunRoute() {
  const { id, runId } = useParams<{ id: string; runId: string }>();
  return <RunCockpitPage taskId={id} runId={runId} />;
}
