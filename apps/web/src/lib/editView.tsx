// An edited photo on screen (ADR-0057): the tone curve and colour matrix as one SVG filter per distinct edit, on the
// GPU, no re-encode; a crop shows the photo enlarged and moved inside its (cropped) box. Shares draw the same maths
// on a canvas (editMath.applyEdit), so the copy looks as it does here.
import type { ReactNode } from "react";
import { colorMatrix, coverScale, isIdentity, toneLut, type Edit } from "./editMath";

const SVG_NS = "http://www.w3.org/2000/svg";

function key(e: Edit): string {
  const sorted = (o: unknown): unknown =>
    o && typeof o === "object" ? Object.fromEntries(Object.entries(o as Record<string, unknown>).sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => [k, sorted(v)])) : o;
  const tone: Partial<Edit> = { ...e };
  delete tone.crop;
  delete tone.straighten;
  const text = JSON.stringify(sorted(tone));
  let h = 5381;
  for (let i = 0; i < text.length; i++) h = ((h * 33) ^ text.charCodeAt(i)) >>> 0;
  return h.toString(36);
}

function host(): SVGSVGElement {
  let svg = document.getElementById("opa-edit-filters") as SVGSVGElement | null;
  if (!svg) {
    svg = document.createElementNS(SVG_NS, "svg");
    svg.id = "opa-edit-filters";
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("width", "0");
    svg.setAttribute("height", "0");
    svg.style.position = "absolute";
    document.body.appendChild(svg);
  }
  return svg;
}

/** The edit's light and colour as a CSS `filter` value, or nothing when it changes neither. */
export function editFilter(e: Edit | null | undefined): string | undefined {
  if (!e) return undefined;
  const tone = { ...e, crop: undefined, straighten: undefined };
  if (isIdentity(tone)) return undefined;
  const id = `opa-e-${key(e)}`;
  if (!document.getElementById(id)) {
    const f = document.createElementNS(SVG_NS, "filter");
    f.id = id;
    f.setAttribute("color-interpolation-filters", "sRGB");
    const ct = document.createElementNS(SVG_NS, "feComponentTransfer");
    const table = toneLut(e)
      .map((v) => (v / 255).toFixed(4))
      .join(" ");
    for (const ch of ["feFuncR", "feFuncG", "feFuncB"]) {
      const fn = document.createElementNS(SVG_NS, ch);
      fn.setAttribute("type", "table");
      fn.setAttribute("tableValues", table);
      ct.appendChild(fn);
    }
    const cm = document.createElementNS(SVG_NS, "feColorMatrix");
    cm.setAttribute("type", "matrix");
    cm.setAttribute("values", colorMatrix(e).join(" "));
    f.append(ct, cm);
    host().appendChild(f);
  }
  return `url(#${id})`;
}

const pct = (v: number) => `${Math.round(v * 1e4) / 100}%`;

/** A photo with its edit: the children (an img) drawn cropped, straightened and filtered inside the parent's box. */
export function Edited({ edit, children }: { edit: Edit | null | undefined; children: ReactNode }) {
  if (!edit || isIdentity(edit)) return <>{children}</>;
  const c = edit.crop ?? { x: 0, y: 0, w: 1, h: 1 };
  const s = coverScale(edit.straighten ?? 0, c.w, c.h);
  return (
    <span className="edit-frame">
      <span
        data-edit-inner=""
        className="edit-inner"
        style={{
          width: pct(1 / c.w),
          height: pct(1 / c.h),
          left: pct(-c.x / c.w),
          top: pct(-c.y / c.h),
          filter: editFilter(edit),
          transform: edit.straighten ? `rotate(${edit.straighten}deg) scale(${s})` : undefined,
          transformOrigin: `${pct(c.x + c.w / 2)} ${pct(c.y + c.h / 2)}`,
        }}
      >
        {children}
      </span>
    </span>
  );
}
