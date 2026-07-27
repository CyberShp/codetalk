export function providerConfigValue(config: Record<string, unknown>): string {
  return String(config.provider_ref ?? config.provider ?? "builtin-llm");
}

export function providerSelectionPatch(
  providerRef: string,
  isV3: boolean,
): Record<string, unknown> {
  return isV3
    ? { provider_ref: providerRef, provider: undefined, mcp_profiles: [] }
    : { provider: providerRef, mcp_profiles: [] };
}
