import { ArtifactProfilesView } from "@/app/workbench/artifact-profiles-view";

export default function ArtifactProfilesPage() {
  return (
    <main className="ct-asset-page">
      <header className="ct-v2-page-header">
        <div>
          <h1>交付件档案</h1>
        </div>
      </header>
      <ArtifactProfilesView />
    </main>
  );
}
