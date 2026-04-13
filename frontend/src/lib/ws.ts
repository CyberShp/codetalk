import type { LogEntry } from "./types";

const WS_BASE = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000";

export function connectTaskLogs(
  taskId: string,
  onMessage: (log: LogEntry) => void,
): WebSocket {
  const ws = new WebSocket(`${WS_BASE}/ws/tasks/${taskId}/logs`);

  ws.onmessage = (event) => {
    try {
      const log: LogEntry = JSON.parse(event.data);
      onMessage(log);
    } catch {
      onMessage({
        timestamp: new Date().toISOString(),
        level: "info",
        message: event.data,
      });
    }
  };

  return ws;
}
