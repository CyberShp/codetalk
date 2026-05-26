import { ChatProvider } from "@/lib/chatContext";

export default function WorkspacesLayout({ children }: { children: React.ReactNode }) {
  return <ChatProvider>{children}</ChatProvider>;
}
