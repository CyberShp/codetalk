import { ArtifactProfilesView } from "@/app/workbench/artifact-profiles-view";

export default function ArtifactProfilesPage() {
  return (
    <main className="min-h-screen bg-surface px-6 py-5 text-on-surface">
      <header className="mb-5 border-b border-outline-variant/30 pb-4">
        <h1 className="text-xl font-semibold">交付件档案</h1>
        <p className="mt-1 text-sm text-on-surface-variant">定义每类交付件的文件名、格式、结构和内容约定。</p>
      </header>
      <ArtifactProfilesView />
    </main>
  );
}
