// 「複製到資料夾…」 (audit rounds 58-63): the chosen photos become a plan that copies them into a folder you pick, with
// the originals untouched; you approve it on the plan page. The search bar and the timeline bar share it.
import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../lib/api";
import { Button } from "./Button";

export function CopyToFolderButton({ select }: { select: () => Promise<{ id: string }> }) {
  const { t } = useTranslation();
  const nav = useNavigate();
  const [moving, setMoving] = useState(false);
  const moveRef = useRef<HTMLSpanElement>(null);
  return (
    <>
      <span ref={moveRef}>
        <Button variant="ghost" onClick={() => setMoving(true)}>
          {t("batch.moveToFolder")}
        </Button>
      </span>
      {moving && (
        <MoveToFolder
          onCancel={() => {
            setMoving(false);
            requestAnimationFrame(() => moveRef.current?.querySelector("button")?.focus()); // back where it opened
          }}
          onCreate={async (target) => {
            const s = await select();
            const r = await api.selectionOrganize(s.id, target);
            void nav({ to: "/plans/$planId", params: { planId: r.plan_id } });
          }}
        />
      )}
    </>
  );
}

function MoveToFolder({ onCancel, onCreate }: { onCancel: () => void; onCreate: (t: { root_alias: string; sub_path: string }) => Promise<void> }) {
  const { t } = useTranslation();
  const roots = useQuery({ queryKey: ["roots"], queryFn: api.roots });
  const writable = (roots.data ?? []).filter((r) => r.mode === "rw");
  const [root, setRoot] = useState<string>("");
  const [sub, setSub] = useState("");
  const create = useMutation({ mutationFn: () => onCreate({ root_alias: root || writable[0]?.alias, sub_path: sub }) });
  // the dialog takes the keyboard as it opens (audit round 58: focus stayed on the button behind it)
  const first = useRef<HTMLSelectElement>(null);
  useEffect(() => first.current?.focus(), []);
  return (
    <div role="dialog" aria-modal="true" aria-labelledby="mtf-h" className="fixed inset-0 z-40 grid place-items-center bg-black/50" onKeyDown={(e) => e.key === "Escape" && onCancel()}>
      <div className="w-96 space-y-3 rounded-[var(--radius-lg)] bg-[var(--surface)] p-6">
        <h2 id="mtf-h" className="text-lg font-semibold">{t("batch.moveTitle")}</h2>
        <p className="text-sm text-[var(--text-muted)]">{t("batch.copyNote")}</p>
        <label className="flex flex-col text-sm">
          {t("batch.root")}
          <select ref={first} value={root || writable[0]?.alias || ""} onChange={(e) => setRoot(e.target.value)} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg)] px-2 py-1">
            {writable.map((r) => (
              <option key={r.alias} value={r.alias}>
                {r.alias}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col text-sm">
          {t("batch.subPath")}
          <input value={sub} onChange={(e) => setSub(e.target.value)} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg)] px-2 py-1" />
        </label>
        <div className="flex gap-2">
          <Button
            disabledReason={!writable.length ? { code: "FS_NOT_WRITABLE", message: t("organize.noWritableTarget") } : create.isPending ? { code: "BUSY", message: t("common.working") } : null}
            onClick={() => create.mutate()}
          >
            {t("batch.createPlan")}
          </Button>
          <Button variant="ghost" onClick={onCancel}>
            {t("batch.cancel")}
          </Button>
        </div>
      </div>
    </div>
  );
}
