/**
 * WebSocket client for repo chat streaming.
 *
 * Protocol (Server → Client):
 *   {"type": "chunk", "content": "..."}
 *   {"type": "research_round", "round": 2, "max": 5}
 *   {"type": "done"}
 *   {"type": "error", "message": "..."}
 *
 * Protocol (Client → Server):
 *   {"action": "chat", "messages": [...], "file_path": "...", "included_files": [...], "deep_research": false}
 *   {"action": "stop"}
 */

const BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export interface ChatWsOptions {
  repoId: string;
  onChunk: (content: string) => void;
  onDone: () => void;
  onError: (message: string) => void;
  onResearchRound?: (round: number, max: number) => void;
}

export interface ChatWsSendParams {
  messages: Array<{ role: string; content: string }>;
  file_path?: string;
  included_files?: string[];
  deep_research?: boolean;
}

export class ChatWsClient {
  private ws: WebSocket | null = null;
  private readonly options: ChatWsOptions;
  /** Set to true after the server sends {"type":"done"} so a subsequent close event is expected. */
  private doneReceived = false;
  /** Set to true when close() is called by application code to prevent spurious onError. */
  private intentionallyClosed = false;

  constructor(options: ChatWsOptions) {
    this.options = options;
  }

  /**
   * Connect to the backend WS endpoint.
   * Returns true on success, false on failure (caller should fall back to HTTP).
   *
   * After the promise resolves true, persistent runtime close/error handlers are
   * installed so the UI is notified of unexpected disconnects during streaming.
   */
  connect(): Promise<boolean> {
    return new Promise((resolve) => {
      try {
        const wsBase = BASE.replace(/^http/, "ws");
        const url = `${wsBase}/api/repos/${this.options.repoId}/chat/ws`;
        const ws = new WebSocket(url);

        const onOpen = () => {
          cleanup();

          // Runtime close handler: fires on unexpected disconnect
          ws.addEventListener("close", () => {
            if (!this.intentionallyClosed && !this.doneReceived) {
              this.ws = null;
              this.options.onError("Connection closed unexpectedly.");
            } else {
              // Clean close after "done" or explicit close() — nothing to do
              this.ws = null;
            }
          });

          // Runtime error handler: fires on network-level WS errors
          ws.addEventListener("error", () => {
            if (!this.intentionallyClosed && !this.doneReceived) {
              this.ws = null;
              this.options.onError("WebSocket connection error.");
            }
          });

          resolve(true);
        };

        const onError = () => {
          cleanup();
          ws.close();
          resolve(false);
        };

        const onClose = () => {
          cleanup();
          resolve(false);
        };

        const cleanup = () => {
          ws.removeEventListener("open", onOpen);
          ws.removeEventListener("error", onError);
          ws.removeEventListener("close", onClose);
        };

        ws.addEventListener("open", onOpen);
        ws.addEventListener("error", onError);
        ws.addEventListener("close", onClose);

        ws.addEventListener("message", (event: MessageEvent) => {
          this.handleMessage(event);
        });

        this.ws = ws;
      } catch {
        resolve(false);
      }
    });
  }

  private handleMessage(event: MessageEvent): void {
    let data: { type: string; content?: string; message?: string; round?: number; max?: number };
    try {
      data = JSON.parse(event.data as string);
    } catch {
      return;
    }

    switch (data.type) {
      case "chunk":
        if (data.content != null) {
          this.options.onChunk(data.content);
        }
        break;
      case "done":
        this.doneReceived = true;
        this.options.onDone();
        break;
      case "error":
        this.options.onError(data.message ?? "Unknown error");
        break;
      case "research_round":
        if (this.options.onResearchRound != null && data.round != null && data.max != null) {
          this.options.onResearchRound(data.round, data.max);
        }
        break;
    }
  }

  /** Send a chat message. Requires an active connection. */
  send(params: ChatWsSendParams): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    // Reset so the runtime close/error handler fires onError on unexpected
    // disconnect during this new stream (not silenced by a prior done).
    this.doneReceived = false;
    this.ws.send(JSON.stringify({ action: "chat", ...params }));
  }

  /** Ask the backend to abort the current stream. */
  stop(): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    this.ws.send(JSON.stringify({ action: "stop" }));
  }

  /** Close the connection cleanly without triggering onError. */
  close(): void {
    if (this.ws) {
      this.intentionallyClosed = true;
      this.ws.close();
      this.ws = null;
    }
  }

  /** True when the underlying WebSocket is open. */
  get connected(): boolean {
    return this.ws !== null && this.ws.readyState === WebSocket.OPEN;
  }
}
