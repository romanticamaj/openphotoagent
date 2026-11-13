// The sidebar as a drawer in a narrow window (VIEW-049): a 「選單」 button opens it over the page; Esc or following a
// link closes it. Wide windows show the sidebar as before, and this state does nothing there (CSS decides).
import { useEffect, useState } from "react";

export function useNavDrawer(path: string): { open: boolean; toggle: () => void; close: () => void } {
  const [open, setOpen] = useState(false);
  useEffect(() => setOpen(false), [path]);
  useEffect(() => {
    if (!open) return;
    const on = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [open]);
  return { open, toggle: () => setOpen((x) => !x), close: () => setOpen(false) };
}
