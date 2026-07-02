const PUBLIC_FRONTEND_PORT = "3003";
const PUBLIC_BACKEND_PORT = "3004";

export function resolveReuseExistingServer(env = process.env) {
  return env.CODETALK_REUSE_EXISTING_SERVER === "1";
}

export function isPublicLocalRuntime({ frontendPort, backendPort }) {
  return String(frontendPort ?? "") === PUBLIC_FRONTEND_PORT && String(backendPort ?? "") === PUBLIC_BACKEND_PORT;
}

export function assertCanMutatePublicRuntime({
  env = process.env,
  flowName = "Playwright E2E",
  frontendPort = env.CODETALK_FRONTEND_PORT ?? PUBLIC_FRONTEND_PORT,
  backendPort = env.CODETALK_BACKEND_PORT ?? PUBLIC_BACKEND_PORT,
} = {}) {
  if (!resolveReuseExistingServer(env)) return;
  if (!isPublicLocalRuntime({ frontendPort, backendPort })) return;
  if (env.CODETALK_E2E_ALLOW_PUBLIC_DATA_MUTATION === "1") return;

  throw new Error(
    [
      `${flowName} refused to mutate the public local CodeTalk runtime (${PUBLIC_FRONTEND_PORT}/${PUBLIC_BACKEND_PORT}).`,
      "Use isolated Playwright servers, or set CODETALK_E2E_ALLOW_PUBLIC_DATA_MUTATION=1 when intentionally validating the live local runtime.",
    ].join(" "),
  );
}
