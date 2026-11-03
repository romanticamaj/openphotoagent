// 「連接 AI 助手」 (04 §3.10; 05 §3.2, §3.5, §3.10): the connected apps with their recent calls and images sent,
// revoke in one click, and a wizard that creates a read-only token by default, shows the client config change
// before writing it, and stores the token in the OS keyring (MCP-PAT-001/002/004, MCP-AUD-004).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "../../components/Button";
import { ProblemCard, type Fix, type Problem } from "../../components/ProblemCard";
import { api, type McpClientProposal, type McpSettings, type McpToken } from "../../lib/api";

const CLIENTS = ["claude-desktop", "claude-code", "cursor"] as const;

export function IntegrationsPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const tokens = useQuery({ queryKey: ["mcp-tokens"], queryFn: api.mcpTokens });
  const settings = useQuery({ queryKey: ["mcp-settings"], queryFn: api.mcpSettings });
  const [wizard, setWizard] = useState(false);
  const revoke = useMutation({
    mutationFn: (id: string) => api.revokeMcpToken(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["mcp-tokens"] }),
  });
  const save = useMutation({
    mutationFn: (patch: Record<string, unknown>) => api.putMcpSettings(patch),
    // switches move when clicked; the server's answer replaces the guess
    onMutate: (patch) => qc.setQueryData(["mcp-settings"], (old: McpSettings | undefined) => (old ? { ...old, ...patch } : old)),
    onSuccess: (data) => qc.setQueryData(["mcp-settings"], data),
    onError: () => qc.invalidateQueries({ queryKey: ["mcp-settings"] }),
  });
  const live = (tokens.data ?? []).filter((x) => !x.revoked);
  return (
    <section className="mx-auto max-w-3xl space-y-6 p-8" aria-labelledby="mcp-title">
      <header className="flex items-center justify-between">
        <h1 id="mcp-title" className="text-2xl font-semibold">{t("mcp.title")}</h1>
        <Button onClick={() => setWizard(true)}>{t("mcp.connect")}</Button>
      </header>
      <p className="text-sm text-[var(--text-muted)]">{t("mcp.intro")}</p>
      {wizard && <ConnectWizard onDone={() => { setWizard(false); void qc.invalidateQueries({ queryKey: ["mcp-tokens"] }); }} />}
      <ul className="space-y-3" aria-label={t("mcp.apps")}>
        {live.length === 0 && <li className="text-sm">{t("mcp.none")}</li>}
        {live.map((x) => (
          <AppRow
            key={x.id}
            token={x}
            onRevoke={() => revoke.mutate(x.id)}
            onEnable={() => save.mutateAsync({ enabled: true })}
            onRegenerate={() => setWizard(true)}
          />
        ))}
      </ul>
      {settings.data && (
        <fieldset className="space-y-2 rounded-[var(--radius-md)] border border-[var(--border)] p-4">
          <legend className="px-1 text-sm font-semibold">{t("mcp.workspace")}</legend>
          <Switch id="enable-mcp" label={t("mcp.enabled")} on={settings.data.enabled !== false} onChange={(v) => save.mutate({ enabled: v })} />
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={Boolean(settings.data.allow_apply_requests)}
              onChange={(e) => save.mutate({ allow_apply_requests: e.target.checked })}
            />
            {t("mcp.allowApply")}
          </label>
          <label className="flex items-center gap-2 text-sm">
            {t("mcp.mediaPolicy")}
            <select
              value={String(settings.data.media_policy)}
              onChange={(e) => save.mutate({ media_policy: e.target.value })}
              className="rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1"
            >
              {["none", "thumb_256", "thumb_512"].map((p) => (
                <option key={p} value={p}>{t(`mcp.media.${p}`)}</option>
              ))}
            </select>
          </label>
        </fieldset>
      )}
      <p className="text-xs text-[var(--text-muted)]">{t("mcp.trifecta")}</p>
    </section>
  );
}

// the box follows the click at once and then whatever the server stored
function Switch({ id, label, on, onChange }: { id: string; label: string; on: boolean; onChange: (v: boolean) => void }) {
  const [local, setLocal] = useState(on);
  useEffect(() => setLocal(on), [on]);
  return (
    <label className="flex items-center gap-2 text-sm font-medium">
      <input
        id={id}
        type="checkbox"
        checked={local}
        onChange={(e) => {
          setLocal(e.target.checked);
          onChange(e.target.checked);
        }}
      />
      {label}
    </label>
  );
}

type RowProps = { token: McpToken; onRevoke: () => void; onEnable: () => Promise<unknown>; onRegenerate: () => void };

// 「測試連線」 runs the `mcp.connect` preflight (06 §4.2): a blocker shows its card, and each fix does what it says
function AppRow({ token, onRevoke, onEnable, onRegenerate }: RowProps) {
  const { t } = useTranslation();
  const [result, setResult] = useState<Problem[] | null>(null);
  const test = useMutation({
    mutationFn: () => api.preflightFull("mcp.connect", { token_id: token.id }),
    onSuccess: (pf) => setResult(pf.blockers as Problem[]),
  });
  const onFix = async (f: Fix) => {
    if (f.type === "open_settings" && f.target === "enable-mcp") {
      await onEnable();
      test.mutate();
    } else if (f.target === "regenerate_token") {
      setResult(null);
      onRegenerate();
    }
  };
  return (
    <li data-testid="mcp-app" className="space-y-2 rounded-[var(--radius-md)] border border-[var(--border)] p-3">
      <div className="flex items-center justify-between">
      <div>
        <p className="font-medium">
          {token.name} <span className="text-xs text-[var(--text-muted)]">{`#${token.number}`}</span>
        </p>
        <p className="text-xs text-[var(--text-muted)]" data-testid="mcp-app-usage">
          {t("mcp.usage", { calls: token.calls, images: token.images_returned })} · {token.scopes.join(", ")}
        </p>
      </div>
      <div className="flex gap-2">
        <Button variant="ghost" disabledReason={test.isPending ? { code: "BUSY", message: t("common.working") } : null} onClick={() => test.mutate()}>
          {t("mcp.test")}
        </Button>
        <Button variant="ghost" onClick={onRevoke}>{t("mcp.revoke")}</Button>
      </div>
      </div>
      {result && result.length === 0 && (
        <p role="status" data-testid="mcp-test-ok" className="text-sm">✓ {t("mcp.testOk")}</p>
      )}
      {result?.map((p) => <ProblemCard key={p.code} problem={p} tone="danger" onFix={(f) => void onFix(f)} />)}
    </li>
  );
}

function ConnectWizard({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation();
  const [client, setClient] = useState<(typeof CLIENTS)[number]>("claude-desktop");
  const [media, setMedia] = useState(false);
  const [organize, setOrganize] = useState(false);
  const [created, setCreated] = useState<{ store: string; warning: string | null } | null>(null);
  const [proposal, setProposal] = useState<McpClientProposal | null>(null);
  const [written, setWritten] = useState<{ path: string; backup: string | null } | null>(null);
  const create = useMutation({
    mutationFn: async () => {
      const scopes = ["photos:read", ...(media ? ["photos:media"] : []), ...(organize ? ["organize:propose"] : [])];
      const tok = await api.connectMcpClient(client, scopes);
      setCreated(tok.credential);
      setProposal(await api.mcpClientProposal(client));
    },
  });
  const write = useMutation({ mutationFn: () => api.installMcpClient(client), onSuccess: (r) => setWritten(r) });
  return (
    <section data-testid="mcp-wizard" aria-label={t("mcp.wizard")} className="space-y-4 rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface)] p-5">
      {!created && (
        <>
          <fieldset className="space-y-1">
            <legend className="text-sm font-semibold">{t("mcp.pickClient")}</legend>
            {CLIENTS.map((c) => (
              <label key={c} className="flex items-center gap-2 text-sm">
                <input type="radio" name="client" checked={client === c} onChange={() => setClient(c)} />
                {t(`mcp.client.${c}`)}
              </label>
            ))}
          </fieldset>
          <fieldset className="space-y-1">
            <legend className="text-sm font-semibold">{t("mcp.permissions")}</legend>
            <p className="text-sm">✓ {t("mcp.scope.read")}</p>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={media} onChange={(e) => setMedia(e.target.checked)} />
              {t("mcp.scope.media")}
            </label>
            {media && (
              <p role="note" data-testid="media-egress" className="rounded bg-[var(--warning-bg,transparent)] p-2 text-xs">
                {t("mcp.mediaEgress", { client: t(`mcp.client.${client}`) })}
              </p>
            )}
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={organize} onChange={(e) => setOrganize(e.target.checked)} />
              {t("mcp.scope.propose")}
            </label>
          </fieldset>
          <Button disabledReason={create.isPending ? { code: "BUSY", message: t("common.working") } : null} onClick={() => create.mutate()}>
            {t("mcp.create")}
          </Button>
        </>
      )}
      {created && (
        <p role="status" data-testid="mcp-credential">
          {created.store === "keyring" ? t("mcp.storedKeyring") : t("mcp.storedFile")}
        </p>
      )}
      {proposal && !written && (
        <div className="space-y-2">
          <p className="text-sm">{t("mcp.willWrite", { path: proposal.path })}</p>
          <pre data-testid="mcp-diff" className="overflow-x-auto rounded bg-[var(--surface-2,transparent)] p-3 text-xs" translate="no">
            {(proposal.before ? `- ${JSON.stringify(proposal.before)}\n` : "") + `+ ${JSON.stringify(proposal.after)}`}
          </pre>
          {proposal.command_line && <p className="text-xs" translate="no">{proposal.command_line}</p>}
          <div className="flex gap-2">
            <Button onClick={() => write.mutate()}>{t("mcp.write")}</Button>
            <Button variant="ghost" onClick={onDone}>{t("mcp.skipWrite")}</Button>
          </div>
        </div>
      )}
      {written && (
        <div role="status" data-testid="mcp-written" className="space-y-2 text-sm">
          <p>{t("mcp.written", { path: written.path })}</p>
          {written.backup && <p className="text-xs">{t("mcp.backup", { path: written.backup })}</p>}
          <p>{t("mcp.restartClient", { client: t(`mcp.client.${client}`) })}</p>
          <Button onClick={onDone}>{t("common.done")}</Button>
        </div>
      )}
    </section>
  );
}
