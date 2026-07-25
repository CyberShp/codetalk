import type { ReactNode } from "react";

import { WorkbenchV2RouteGate } from "@/features/release/workbench-v2-route-gate";

export default function SemanticLibraryLayout({ children }: { children: ReactNode }) {
  return <WorkbenchV2RouteGate>{children}</WorkbenchV2RouteGate>;
}
