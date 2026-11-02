// Compare mode (10 §3.9; CMP-001/002): 2–4 photos side by side with one shared zoom and pan, keys 1–4 pick the
// main photo, Esc closes and focus returns to the button that opened it.
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { mediaUrl } from "../../lib/api";

export const COMPARE_MAX = 4;

export function CompareView({ ids, onClose }: { ids: string[]; onClose: () => void }) {
  const { t } = useTranslation();
  const [scale, setScale] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [main, setMain] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; y: number; px: number; py: number } | null>(null);
  useEffect(() => box.current?.focus(), []);
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    } else if (/^[1-4]$/.test(e.key) && Number(e.key) <= ids.length) {
      setMain(Number(e.key) - 1);
    } else if (e.key === "+" || e.key === "=") {
      setScale((s) => Math.min(8, s * 1.5));
    } else if (e.key === "-") {
      setScale((s) => Math.max(1, s / 1.5));
    }
  };
  const transform = `translate(${pan.x}px, ${pan.y}px) scale(${scale})`;
  return (
    <div ref={box} role="dialog" aria-modal="true" aria-label={t("compare.title")} tabIndex={-1} onKeyDown={onKey} className="lightbox">
      <div className="col-span-1 flex items-center gap-2 px-3">
        <button type="button" onClick={onClose} className="px-2 py-1">
          {t("compare.close")}
        </button>
        <span className="flex-1" />
        <button type="button" aria-label={t("lightbox.zoomOut")} onClick={() => setScale((s) => Math.max(1, s / 1.5))} className="px-2">
          −
        </button>
        <button type="button" aria-label={t("lightbox.zoomIn")} onClick={() => setScale((s) => Math.min(8, s * 1.5))} className="px-2">
          +
        </button>
      </div>
      <div
        className={`grid h-full gap-1 ${ids.length > 2 ? "grid-cols-2" : "grid-cols-" + ids.length}`}
        onPointerDown={(e) => (drag.current = { x: e.clientX, y: e.clientY, px: pan.x, py: pan.y })}
        onPointerMove={(e) => drag.current && setPan({ x: drag.current.px + e.clientX - drag.current.x, y: drag.current.py + e.clientY - drag.current.y })}
        onPointerUp={() => (drag.current = null)}
      >
        {ids.map((id, i) => (
          <div key={id} data-testid="compare-pane" aria-current={i === main ? "true" : undefined} className={`relative overflow-hidden ${i === main ? "outline outline-2 outline-[var(--accent)]" : ""}`}>
            <img src={mediaUrl(id, "p1024")} alt={t("compare.photo", { n: i + 1 })} style={{ transform, transformOrigin: "center" }} className="h-full w-full object-contain" draggable={false} />
          </div>
        ))}
      </div>
    </div>
  );
}
