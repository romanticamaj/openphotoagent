// A face cut out of its photo: a square around the face with some margin, from the 1024 px preview so small faces in
// group photos stay sharp. The preview is already rotated, so the normalised bbox applies to it directly.
import { useState, type CSSProperties } from "react";
import { mediaUrl } from "../lib/api";

const MARGIN = 1.6;

export function cropStyle(bbox: number[], [W, H]: [number, number]): { width: string; left: string; top: string } {
  const [x, y, w, h] = bbox;
  const side = Math.max(w * W, h * H) * MARGIN;
  const cx = (x + w / 2) * W;
  const cy = (y + h / 2) * H;
  return {
    width: `${(W * 100) / side}%`,
    left: `${((side / 2 - cx) * 100) / side}%`,
    top: `${((side / 2 - cy) * 100) / side}%`,
  };
}

export function FaceCrop({ assetId, ch, bbox, className }: { assetId: string; ch: string | null; bbox: number[]; className?: string }) {
  const [dims, setDims] = useState<[number, number] | null>(null);
  const style: CSSProperties = dims ? { ...cropStyle(bbox, dims), opacity: 1 } : { opacity: 0 };
  return (
    <span className={`face-crop ${className ?? ""}`}>
      <img
        src={mediaUrl(assetId, "p1024", ch)}
        alt=""
        loading="lazy"
        draggable={false}
        style={style}
        onLoad={(e) => setDims([e.currentTarget.naturalWidth, e.currentTarget.naturalHeight])}
      />
    </span>
  );
}
