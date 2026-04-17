"use client";

import { useParams } from "next/navigation";
import WikiViewer from "@/components/ui/WikiViewer";

export default function WikiPage() {
  const params = useParams();
  const taskId = params.id as string;

  return <WikiViewer taskId={taskId} standalone />;
}
