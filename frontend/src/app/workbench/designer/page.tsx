import { redirect } from "next/navigation";

export default async function WorkflowDesignerPage({
  searchParams,
}: {
  searchParams: Promise<{ workflow?: string }>;
}) {
  const { workflow } = await searchParams;
  if (workflow) {
    redirect(`/workflows/${encodeURIComponent(workflow)}/designer`);
  }
  redirect("/workflows/new");
}
