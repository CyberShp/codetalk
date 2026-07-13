import { TaskChatProvider } from "@/lib/taskChatContext";
import { WorkbenchV2RouteGate } from "@/features/release/workbench-v2-route-gate";

export default function TasksLayout({ children }: { children: React.ReactNode }) {
  return <WorkbenchV2RouteGate legacyDestination="/workbench"><TaskChatProvider>{children}</TaskChatProvider></WorkbenchV2RouteGate>;
}
