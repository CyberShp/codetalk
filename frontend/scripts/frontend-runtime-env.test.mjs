import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { buildFrontendRuntimeEnv } from "./frontend-runtime-env.mjs";

test("derives public API URL from CodeTalk backend port when no explicit override is set", () => {
  const env = buildFrontendRuntimeEnv({
    CODETALK_BACKEND_PORT: "3004",
    CODETALK_FRONTEND_PORT: "3003",
  });

  assert.equal(env.NEXT_PUBLIC_API_URL, "http://localhost:3004");
});

test("keeps an explicit public API URL override", () => {
  const env = buildFrontendRuntimeEnv({
    CODETALK_BACKEND_PORT: "3004",
    NEXT_PUBLIC_API_URL: "http://localhost:3888",
  });

  assert.equal(env.NEXT_PUBLIC_API_URL, "http://localhost:3888");
});

test("uses the configured browser host for derived local API URLs", () => {
  const env = buildFrontendRuntimeEnv({
    CODETALK_BACKEND_PORT: "3104",
    CODETALK_BROWSER_HOST: "127.0.0.1",
  });

  assert.equal(env.NEXT_PUBLIC_API_URL, "http://127.0.0.1:3104");
});

test("next-with-port passes the derived frontend runtime env to Next", () => {
  const source = readFileSync(new URL("./next-with-port.mjs", import.meta.url), "utf8");

  assert.match(source, /buildFrontendRuntimeEnv/);
  assert.match(source, /env:\s*buildFrontendRuntimeEnv/);
});
