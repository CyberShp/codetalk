import { TaskChatProvider } from "@/lib/taskChatContext";

export default function TasksLayout({ children }: { children: React.ReactNode }) {
  return <TaskChatProvider>{children}</TaskChatProvider>;
}
