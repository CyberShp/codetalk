import net from "node:net";

export function preflightHosts(host, clientHost = host) {
  if (host.toLowerCase() === "localhost") {
    return ["127.0.0.1", "::1"];
  }
  if (host === "0.0.0.0" && clientHost.toLowerCase() === "localhost") {
    return ["0.0.0.0", "::1"];
  }
  return [host];
}

async function probePort({ probeHost, originalHost, numericPort, envName, serviceName }) {
  const server = net.createServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(numericPort, probeHost, () => resolve());
  })
    .then(
      () =>
        new Promise((resolve) => {
          server.close(() => resolve());
        }),
    )
    .catch((error) => {
      if (error?.code === "EADDRNOTAVAIL" && probeHost === "::1") {
        return;
      }
      if (error?.code === "EADDRINUSE") {
        console.error(
          `${serviceName} port ${originalHost}:${numericPort} is already in use. Stop the process using it or set ${envName} to another port.`,
        );
        process.exit(1);
      }
      console.error(
        `${serviceName} port ${originalHost}:${numericPort} is not available (${error?.code ?? error?.message ?? error}). Set ${envName} to another port or host.`,
      );
      process.exit(1);
    });
}

export async function assertPortAvailable({ host, port, envName, serviceName, clientHost = host }) {
  const numericPort = Number(port);
  if (!Number.isInteger(numericPort) || numericPort <= 0 || numericPort > 65535) {
    console.error(
      `${serviceName} port "${port}" is invalid. Set ${envName} to an integer between 1 and 65535.`,
    );
    process.exit(1);
  }

  for (const probeHost of preflightHosts(host, clientHost)) {
    await probePort({ probeHost, originalHost: host, numericPort, envName, serviceName });
  }
}
