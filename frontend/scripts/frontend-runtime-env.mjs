export function buildFrontendRuntimeEnv(env = process.env) {
  const backendPort = env.CODETALK_BACKEND_PORT ?? "3004";
  const browserHost = env.CODETALK_BROWSER_HOST ?? "localhost";
  return {
    ...env,
    NEXT_PUBLIC_API_URL:
      env.NEXT_PUBLIC_API_URL ?? `http://${browserHost}:${backendPort}`,
  };
}
