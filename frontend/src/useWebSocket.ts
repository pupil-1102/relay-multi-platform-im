import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";

type Handler = (event: { type: string; data: any }) => void;

export function useWebSocket(onEvent: Handler, onStatus: (connected: boolean) => void, enabled = true) {
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!enabled) {
      onStatus(false);
      return;
    }
    let socket: WebSocket | null = null;
    let retryTimer: number | undefined;
    let stopped = false;
    let attempts = 0;

    const connect = () => {
      if (stopped) return;
      const protocol = window.location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${protocol}://${window.location.host}/ws`);

      socket.onopen = () => {
        attempts = 0;
        onStatus(true);
      };
      socket.onmessage = (raw) => {
        try {
          const event = JSON.parse(raw.data);
          if (event.type === "hello" || event.type === "pong" || event.type === "ready") return;
          if (event.type === "message.created") {
            queryClient.invalidateQueries({ queryKey: ["messages"] });
            queryClient.invalidateQueries({ queryKey: ["overview"] });
          }
          if (["task.created", "task.updated", "task.action"].includes(event.type)) {
            queryClient.invalidateQueries({ queryKey: ["tasks"] });
            queryClient.invalidateQueries({ queryKey: ["task"] });
            queryClient.invalidateQueries({ queryKey: ["overview"] });
          }
          onEvent(event);
        } catch {
          /* ignore malformed frames */
        }
      };
      socket.onerror = () => onStatus(false);
      socket.onclose = () => {
        onStatus(false);
        if (stopped) return;
        attempts += 1;
        retryTimer = window.setTimeout(connect, Math.min(1000 * attempts, 8000));
      };
    };

    connect();
    return () => {
      stopped = true;
      if (retryTimer) window.clearTimeout(retryTimer);
      socket?.close();
    };
  }, [enabled, onEvent, onStatus, queryClient]);
}
