"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import WikiViewer from "@/components/ui/WikiViewer";
import { api } from "@/lib/api";

export default function WikiPage() {
  const params = useParams();
  const taskId = params.id as string;
  const [repoId, setRepoId] = useState<string | undefined>(undefined);

  useEffect(() => {
    api.tasks.get(taskId).then((task) => {
      if (task.repository_id) setRepoId(task.repository_id);
    }).catch(() => {/* degraded: no regenerate button */});
  }, [taskId]);

  return <WikiViewer taskId={taskId} repoId={repoId} standalone />;
}
