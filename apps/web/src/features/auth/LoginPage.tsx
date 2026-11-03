// Control-plane sign-in (v0.5; 02 §6.3): e-mail + password, or the organisation's OIDC provider.
// The desktop app and the all-in-one never show this page (single user, no login; 08 §1.3).
import { useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "../../components/Button";
import { ApiError, api } from "../../lib/api";
import { setCsrf } from "../../lib/platform";

function nextPath(): string {
  const n = new URLSearchParams(window.location.search).get("next") || "/";
  return n.startsWith("/") && !n.startsWith("//") ? n : "/"; // never an open redirect
}

export function LoginPage() {
  const { t } = useTranslation();
  const caps = useQuery({ queryKey: ["capabilities"], queryFn: api.capabilities, staleTime: Infinity });
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api.login(email, password);
      setCsrf(r.csrf);
      window.location.assign(nextPath());
    } catch (err) {
      setError(err instanceof ApiError && err.status === 429 ? "auth.rateLimited" : "auth.failed");
    } finally {
      setBusy(false);
    }
  };
  return (
    <main className="mx-auto mt-24 max-w-sm space-y-4 p-6" aria-labelledby="login-h">
      <h1 id="login-h" className="text-xl font-semibold">
        {t("auth.title")}
      </h1>
      <form onSubmit={submit} className="space-y-3">
        <label className="block text-sm">
          {t("auth.email")}
          <input type="email" required autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} className="mt-1 w-full rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface-1)] px-2 py-1" />
        </label>
        <label className="block text-sm">
          {t("auth.password")}
          <input type="password" required autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} className="mt-1 w-full rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface-1)] px-2 py-1" />
        </label>
        {error ? (
          <p role="alert" className="text-sm text-[var(--danger)]">
            {t(error)}
          </p>
        ) : null}
        <Button type="submit" disabledReason={busy ? { code: "SIGNING_IN", message: t("auth.signingIn") } : null}>
          {t("auth.submit")}
        </Button>
      </form>
      {caps.data?.login?.oidc ? (
        <a href="/api/v1/auth/oidc/start" className="block text-sm underline">
          {t("auth.sso")}
        </a>
      ) : null}
    </main>
  );
}
