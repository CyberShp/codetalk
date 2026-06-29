import net from "node:net";

export async function assertPortAvailable({ host, port, envName, serviceName }) {
  const numericPort = Number(port);
  if (!Number.isInteger(numericPort) || numericPort <= 0 || numericPort > 65535) {
    console.error(
      `${serviceName} port "${port}" is invalid. Set ${envName} to an integer between 1 and 65535.`,
    );
    process.exit(1);
  }

  const server = net.createServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(numericPort, host, () => resolve());
  })
    .then(
      () =>
        new Promise((resolve) => {
          server.close(() => resolve());
        }),
    )
    .catch((error) => {
      if (error?.code === "EADDRINUSE") {
        console.error(
          `${serviceName} port ${host}:${numericPort} is already in use. Stop the process using it or set ${envName} to another port.`,
        );
        process.exit(1);
      }
      console.error(
        `${serviceName} port ${host}:${numericPort} is not available (${error?.code ?? error?.message ?? error}). Set ${envName} to another port or host.`,
      );
      process.exit(1);
    });
}
