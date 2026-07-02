import fs from "node:fs";

export function readSecretValue(name, env = process.env) {
  const direct = env[name];
  if (typeof direct === "string" && direct.length > 0) {
    return direct;
  }

  const filePath = env[`${name}_FILE`];
  if (typeof filePath !== "string" || filePath.length === 0) {
    return "";
  }

  return fs.readFileSync(filePath, "utf8").trim();
}
