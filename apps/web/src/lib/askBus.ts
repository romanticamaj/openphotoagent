// 「問 AI」 from anywhere (ASK-UI-020): a page hands a question to the ask panel, which the shell opens with it filled in.
import { useEffect, useRef } from "react";

const EVENT = "opa:ask";

export function askAbout(question: string): void {
  window.dispatchEvent(new CustomEvent<string>(EVENT, { detail: question }));
}

export function useAskRequest(on: (question: string) => void): void {
  const ref = useRef(on);
  ref.current = on;
  useEffect(() => {
    const h = (e: Event) => ref.current((e as CustomEvent<string>).detail);
    window.addEventListener(EVENT, h);
    return () => window.removeEventListener(EVENT, h);
  }, []);
}
