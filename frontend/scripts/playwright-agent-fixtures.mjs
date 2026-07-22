export function buildExternalAgentProviders(existing, slowAgentCommand, includeSyntheticSlowAgent) {
  const raw = String(existing ?? "").trim();
  if (!includeSyntheticSlowAgent) return raw;

  const slowAgent = {
    id: "slow-agent",
    command: slowAgentCommand,
    prompt_transport: "stdin",
    supports_artifact_export: true,
    supports_json_output: true,
  };
  if (!raw) return JSON.stringify([slowAgent]);
  try {
    const parsed = JSON.parse(raw);
    const providers = Array.isArray(parsed) ? parsed : [parsed];
    if (providers.some((item) => item && typeof item === "object" && item.id === "slow-agent")) {
      return raw;
    }
    return JSON.stringify([...providers, slowAgent]);
  } catch {
    return JSON.stringify([
      { id: "external-agent", command: raw, prompt_transport: "stdin" },
      slowAgent,
    ]);
  }
}
