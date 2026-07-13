import type { ReactNode } from "react";

import { WorkbenchV2RouteGate } from "@/features/release/workbench-v2-route-gate";

export default function WorkflowsLayout({ children }: { children: ReactNode }) {
  return <WorkbenchV2RouteGate legacyDestination="/workbench/designer">{children}</WorkbenchV2RouteGate>;
}
