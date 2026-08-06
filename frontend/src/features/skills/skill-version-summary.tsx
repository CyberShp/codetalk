"use client";

import { CheckCircle2, FileArchive, ShieldCheck } from "lucide-react";
import type { SkillVersion } from "@/lib/types/skill";

export function SkillVersionSummary({ version }: { version: SkillVersion }) {
  return (
    <section className="ct-skill-version-summary" aria-label="Skill version">
      <header>
        <FileArchive size={18} />
        <div>
          <strong>{version.skill_id}</strong>
          <span>{version.version_id}</span>
        </div>
      </header>
      <dl>
        <div>
          <dt>Content digest</dt>
          <dd>{version.content_digest}</dd>
        </div>
        <div>
          <dt>Review digest</dt>
          <dd>{version.review_evidence_digest}</dd>
        </div>
      </dl>
      <footer>
        <span><ShieldCheck size={14} /> Reviewed</span>
        <span><CheckCircle2 size={14} /> Immutable</span>
      </footer>
    </section>
  );
}
