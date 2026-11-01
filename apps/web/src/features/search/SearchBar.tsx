// Top-bar search (05 §2): usable as soon as the embedding model is ready; until then it says why (invariant 8).
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../../lib/api";

const MODEL_GATES = new Set(["MODEL_NOT_INSTALLED", "MODEL_DOWNLOADING"]);

export function SearchBar() {
  const { t } = useTranslation();
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const pf = useQuery({
    queryKey: ["preflight", "search"],
    queryFn: () => api.preflight("search"),
    refetchInterval: (query) => (query.state.data?.blockers.some((b) => MODEL_GATES.has(b.code)) ? 1000 : 15_000),
  });
  const gate = pf.data?.blockers.find((b) => MODEL_GATES.has(b.code));
  const reason = gate ? t(`search.unavailable.${gate.code}`, { pct: gate.params?.pct ?? 0 }) : null;
  return (
    <form
      role="search"
      className="flex flex-1 flex-col"
      onSubmit={(e) => {
        e.preventDefault();
        if (q.trim()) void nav({ to: "/search", search: { q: q.trim() } });
      }}
    >
      <input
        type="search"
        aria-label={t("search.label")}
        placeholder={t("search.placeholder")}
        value={q}
        onChange={(e) => setQ(e.target.value)}
        // eslint-disable-next-line opa/no-bare-disabled -- the reason is rendered next to it and linked below
        disabled={Boolean(gate)}
        aria-describedby={gate ? "search-why" : undefined}
        className="w-full max-w-md rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg)] px-3 py-1 disabled:opacity-60"
      />
      {reason && (
        <span id="search-why" className="text-xs text-[var(--text-muted)]">
          {reason}
        </span>
      )}
    </form>
  );
}
