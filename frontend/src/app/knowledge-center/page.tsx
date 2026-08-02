import { KnowledgeCenterView } from "@/app/workbench/knowledge-center-view";

export default function KnowledgeCenterPage() {
  return (
    <main className="min-h-screen bg-surface px-6 py-5 text-on-surface">
      <header className="mb-5 border-b border-outline-variant/30 pb-4">
        <h1 className="text-xl font-semibold">经验知识库</h1>
        <p className="mt-1 text-sm text-on-surface-variant">管理历史问题、经验模式、测试资产与导入记录。</p>
      </header>
      <KnowledgeCenterView />
    </main>
  );
}
