// Faces marked on the photo (VIEW-026): in a group photo, where the person is. The photo is drawn contained and
// centred in the stage, so a normalised box maps onto the drawn rectangle, not onto the stage.
import { Link } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";

function useHoveredFace(): string | null {
  const [id, setId] = useState<string | null>(null);
  useEffect(() => {
    const h = (e: Event) => setId(((e as CustomEvent).detail as string | null) ?? null);
    window.addEventListener("opa:face-hover", h);
    return () => window.removeEventListener("opa:face-hover", h);
  }, []);
  return id;
}
import { useTranslation } from "react-i18next";

type Size = { w: number; h: number };

export function faceBoxStyle(bbox: number[], stage: Size, photo: Size): { left: number; top: number; width: number; height: number } {
  const k = Math.min(stage.w / photo.w, stage.h / photo.h);
  const dw = photo.w * k;
  const dh = photo.h * k;
  const ox = (stage.w - dw) / 2;
  const oy = (stage.h - dh) / 2;
  const [x, y, w, h] = bbox;
  return { left: ox + x * dw, top: oy + y * dh, width: w * dw, height: h * dh };
}

export function FaceBoxes({
  photo,
  people,
  unknown = [],
}: {
  photo: Size;
  people: Array<{ id: string; name: string | null; bbox: number[] }>;
  unknown?: Array<{ id?: string; bbox: number[]; small: boolean }>; // faces of nobody yet: marked, not linked (VIEW-029)
}) {
  const hovered = useHoveredFace(); // the row being named in the info panel (VIEW-033)
  const { t } = useTranslation();
  const box = useRef<HTMLDivElement>(null);
  const [stage, setStage] = useState<Size | null>(null);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const measure = () => setStage({ w: el.clientWidth, h: el.clientHeight });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return (
    <div ref={box} className="face-boxes" data-testid="face-boxes">
      {stage
        ? unknown.map((u, i) => (
            <span
              key={`u${i}`}
              aria-hidden="true"
              className={`face-box-unknown${u.small ? " is-small" : ""}${u.id && u.id === hovered ? " is-hovered" : ""}`}
              style={faceBoxStyle(u.bbox, stage, photo)}
            />
          ))
        : null}
      {stage
        ? people.map((p) => (
            <Link key={p.id} to="/people/$personId" params={{ personId: p.id }} className="face-box" style={faceBoxStyle(p.bbox, stage, photo)} translate="no">
              <span>{p.name ?? t("faces.unnamed")}</span>
            </Link>
          ))
        : null}
    </div>
  );
}
