// One shared SSE connection (04 §5.3): components subscribe to event kinds such as `model.installed`.
import { useEffect, useRef } from "react";
import { endpoint, platform } from "./platform";

let source: EventSource | null = null;
let desktopStream: AbortController | null = null;
const listeners = new Map<string, Set<(data: unknown) => void>>();

function dispatch(kind: string, raw: string) {
  let data: unknown = null;
  try {
    data = JSON.parse(raw);
  } catch {
    /* ignore malformed */
  }
  for (const f of listeners.get(kind) ?? []) f(data);
}

/** SSE over fetch so the desktop launch token can ride in a header (EventSource cannot set headers, 08 §1.3).
 *  Resolves when the stream ends. */
export async function readSse(path: string, on: (kind: string, data: unknown) => void, signal: AbortSignal): Promise<void> {
  const { url, headers } = await endpoint(path);
  const r = await fetch(url, { headers: { ...headers, Accept: "text/event-stream" }, signal });
  if (!r.ok || !r.body) return;
  const reader = r.body.pipeThrough(new TextDecoderStream()).getReader();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (value) buf += value;
    let cut: number;
    while ((cut = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, cut);
      buf = buf.slice(cut + 2);
      let kind = "message";
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) kind = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
      }
      if (data.length) {
        let parsed: unknown = null;
        try {
          parsed = JSON.parse(data.join("\n"));
        } catch {
          continue;
        }
        on(kind, parsed);
      }
    }
    if (done) return;
  }
}

function startDesktopStream() {
  desktopStream = new AbortController();
  const ctl = desktopStream;
  const loop = async () => {
    while (!ctl.signal.aborted) {
      try {
        await readSse("/api/v1/events", (k, d) => dispatch(k, JSON.stringify(d)), ctl.signal);
      } catch {
        /* reconnect below */
      }
      await new Promise((r) => setTimeout(r, 1000));
    }
  };
  void loop();
}

function ensure(kind: string) {
  if (platform().backend) {
    if (desktopStream === null) startDesktopStream();
    if (!listeners.has(kind)) listeners.set(kind, new Set());
    return;
  }
  if (typeof EventSource === "undefined") return;
  if (source === null) source = new EventSource("/api/v1/events");
  if (!listeners.has(kind)) {
    listeners.set(kind, new Set());
    source.addEventListener(kind, (e) => dispatch(kind, (e as MessageEvent<string>).data));
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

/** Calls `cb` with each event's data for one `kind` (no debounce). */
export function useServerEvent(kind: string, cb: (data: unknown) => void) {
  const ref = useRef(cb);
  ref.current = cb;
  useEffect(() => {
    const f = (d: unknown) => ref.current(d);
    ensure(kind);
    listeners.get(kind)?.add(f);
    return () => {
      listeners.get(kind)?.delete(f);
    };
  }, [kind]);
}
