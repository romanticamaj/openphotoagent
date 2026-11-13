// 「問 AI」 from anywhere (ASK-UI-020): a page hands a question to the ask panel, which the shell opens with it filled in.
// The panel is loaded when it first opens, after the event: the question waits for it (ASK-UI-021).
import { useEffect, useRef } from "react";

const EVENT = "opa:ask";
let pending: string | null = null;

export function askAbout(question: string): void {
  pending = question;
  window.dispatchEvent(new CustomEvent<string>(EVENT, { detail: question }));
}

/** The question asked before anyone listened for it, once. */
export function takePendingAsk(): string | null {
  const q = pending;
  pending = null;
  return q;
}

/** Called with each question; `take` also clears it, for the one that fills it in (the panel). */
export function useAskRequest(on: (question: string) => void, take = true): void {
  const ref = useRef(on);
  ref.current = on;
  useEffect(() => {
    const h = (e: Event) => {
      if (take) pending = null;
      ref.current((e as CustomEvent<string>).detail);
    };
    window.addEventListener(EVENT, h);
    return () => window.removeEventListener(EVENT, h);
  }, [take]);
}
