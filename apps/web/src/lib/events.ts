// One shared SSE connection (04 §5.3): components subscribe to event kinds such as `model.installed`.
import { useEffect, useRef } from "react";

let source: EventSource | null = null;
const listeners = new Map<string, Set<(data: unknown) => void>>();

function ensure(kind: string) {
  if (typeof EventSource === "undefined") return;
  if (source === null) source = new EventSource("/api/v1/events");
  if (!listeners.has(kind)) {
    listeners.set(kind, new Set());
    source.addEventListener(kind, (e) => {
      let data: unknown = null;
      try {
        data = JSON.parse((e as MessageEvent<string>).data);
      } catch {
        /* ignore malformed */
      }
      for (const f of listeners.get(kind) ?? []) f(data);
    });
  }
}

/** Calls `cb` (debounced) whenever one of `kinds` arrives. */
export function useServerEvents(kinds: string[], cb: () => void, debounceMs = 300) {
  const ref = useRef(cb);
  ref.current = cb;
  const key = kinds.join(",");
  useEffect(() => {
    let h: ReturnType<typeof setTimeout> | null = null;
    const f = () => {
      if (h) clearTimeout(h);
      h = setTimeout(() => ref.current(), debounceMs);
    };
    for (const k of key.split(",")) {
      ensure(k);
      listeners.get(k)?.add(f);
    }
    return () => {
      if (h) clearTimeout(h);
      for (const k of key.split(",")) listeners.get(k)?.delete(f);
    };
  }, [key, debounceMs]);
}
