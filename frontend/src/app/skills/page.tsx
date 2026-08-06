import { Suspense } from "react";
import { SkillCenterPage } from "@/features/skills/skill-center-page";

export default function SkillsPage() {
  return (
    <Suspense fallback={<div className="ct-v2-page-loading">正在读取 Skills...</div>}>
      <SkillCenterPage />
    </Suspense>
  );
}
