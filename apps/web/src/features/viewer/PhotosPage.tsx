import { useState } from "react";
import { Lightbox } from "./Lightbox";
import { PhotoGrid, type GridApi } from "./PhotoGrid";
import { withTransition } from "./transition";

export function PhotosPage() {
  const [open, setOpen] = useState<{ ord: number; grid: GridApi } | null>(null);
  const [back, setBack] = useState<string | null>(null);
  return (
    <>
      <PhotoGrid
        focusAsset={back}
        onOpen={(pos, grid) => withTransition(() => setOpen({ ord: grid.ordinal(pos), grid }), document.activeElement)}
      />
      {open && (
        <Lightbox
          start={open.ord}
          grid={open.grid}
          onClose={(id) => {
            setOpen(null);
            setBack(id);
          }}
        />
      )}
    </>
  );
}
