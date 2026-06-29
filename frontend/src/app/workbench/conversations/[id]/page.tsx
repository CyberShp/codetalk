import { redirect } from "next/navigation";

export default function LegacyConversationPage({ params }: { params: { id: string } }) {
  redirect(`/ai/${params.id}`);
}
