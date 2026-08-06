import type { SkillVersion } from "@/lib/types/skill";

const presetLabels: Record<string, string> = {
  "skill.codetalks-custom": "自定义讲解",
  "skill.codetalks-issue-regression": "Issue 回归",
  "skill.codetalks-module-full-analysis": "模块全量分析",
  "skill.codetalks-root-cause": "根因定位",
  "skill.codetalks-special-risk": "专项风险",
};

export function skillDisplayName(version: Pick<SkillVersion, "skill_id">) {
  return presetLabels[version.skill_id] || version.skill_id.split(".").filter(Boolean).at(-1) || version.skill_id;
}
