import { useEffect, useState } from "react";
import { api, persistToken } from "../api";
import type { AuthMethods } from "../types";

export default function Login({ onSuccess }: { onSuccess: () => void }) {
  const [methods, setMethods] = useState<AuthMethods | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.authMethods().then(setMethods).catch(() => setMethods({
      password_login: true, providers: [],
    }));
  }, []);

  /* Возврат из Keycloak: код приходит параметрами адреса. */
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const code = params.get("code");
    const state = params.get("state");
    if (!code) return;
    setBusy(true);
    api.oidcCallback(code, state || "")
      .then((result) => {
        persistToken(result.token);
        window.history.replaceState({}, "", window.location.pathname);
        onSuccess();
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Вход не удался"))
      .finally(() => setBusy(false));
  }, [onSuccess]);

  async function passwordLogin() {
    setBusy(true);
    setError(null);
    try {
      const result = await api.login(email, password);
      persistToken(result.token);
      onSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось войти");
    } finally {
      setBusy(false);
    }
  }

  async function domainLogin() {
    setBusy(true);
    setError(null);
    try {
      const result = await api.negotiate();
      persistToken(result.token);
      onSuccess();
    } catch (err) {
      setError(err instanceof Error
        ? err.message
        : "Доменный вход не удался. Проверьте, что компьютер в домене");
    } finally {
      setBusy(false);
    }
  }

  async function keycloakLogin() {
    setBusy(true);
    setError(null);
    try {
      const { authorize_url } = await api.oidcStart();
      window.location.href = authorize_url;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Keycloak недоступен");
      setBusy(false);
    }
  }

  const kerberos = methods?.providers.find((p) => p.name === "kerberos" && p.configured);
  const keycloak = methods?.providers.find((p) => p.name === "keycloak" && p.configured);
  const hasDomain = Boolean(kerberos || keycloak);

  return (
    <div className="vs-login">
      <div className="vs-card vs-login-card">
        <h1>График отпусков</h1>

        {hasDomain && (
          <div className="vs-login-domain">
            {kerberos && (
              <button className="vs-btn primary block" onClick={domainLogin} disabled={busy}>
                {kerberos.title || "Вход по доменной учётной записи"}
              </button>
            )}
            {keycloak && (
              <button className="vs-btn block" onClick={keycloakLogin} disabled={busy}>
                {keycloak.title || "Войти через Keycloak"}
              </button>
            )}
          </div>
        )}

        {hasDomain && methods?.password_login && (
          <div className="vs-login-divider"><span>или по паролю</span></div>
        )}

        {methods?.password_login && (
          <>
            <label className="vs-field">
              Почта
              <input type="email" value={email} autoComplete="username"
                onChange={(e) => setEmail(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && passwordLogin()} />
            </label>

            <label className="vs-field">
              Пароль
              <input type="password" value={password} autoComplete="current-password"
                onChange={(e) => setPassword(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && passwordLogin()} />
            </label>

            <button className="vs-btn block" onClick={passwordLogin}
              disabled={busy || !email || !password}>
              {busy ? "Входим…" : "Войти"}
            </button>
          </>
        )}

        {error && <div className="vs-issue"><div className="vs-issue-text">{error}</div></div>}
      </div>
    </div>
  );
}
