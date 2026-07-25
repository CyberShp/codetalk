"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { Loader2 } from "lucide-react";

export function WorkbenchEntryGate({
  destination,
}: {
  destination: string;
}) {
  const router = useRouter();

  useEffect(() => {
    router.replace(destination);
  }, [destination, router]);

  return (
    <div className="flex min-h-[50vh] items-center justify-center gap-2 text-sm text-on-surface-variant" role="status">
      <Loader2 size={18} className="animate-spin" aria-hidden />
      正在进入工作流工作台…
    </div>
  );
}
