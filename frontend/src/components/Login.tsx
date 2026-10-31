import { useEffect, useState } from "react";
import { api, ApiError, persistToken } from "../api";
import type { AuthMethods, DemoRoleCode } from "../types";

export default function Login({ onSuccess }: { onSuccess: () => void }) {
  const [methods, setMethods] = useState<AuthMethods | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [demoOpen, setDemoOpen] = useState(false);
  const [invite, setInvite] = useState(
    () => new URLSearchParams(window.location.search).get("invite"));
  const [repeat, setRepeat] = useState("");
  const [inviteDead, setInviteDead] = useState(false);

  useEffect(() => {
    api.authMethods().then(setMethods).catch(() => setMethods({
      password_login: true, providers: [], demo: { enabled: false, roles: [] },
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

  async function demoLogin(role: DemoRoleCode) {
    setBusy(true);
    setError(null);
    try {
      const result = await api.demoLogin(role);
      persistToken(result.token, result.scheme);
      onSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось открыть демо-режим");
    } finally {
      setBusy(false);
    }
  }

  async function acceptInvite() {
    if (!invite) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.acceptInvitation(invite, password);
      persistToken(result.token);
      window.history.replaceState({}, "", window.location.pathname);
      onSuccess();
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) setInviteDead(true);
      else setError(err instanceof Error ? err.message : "Не удалось задать пароль");
    } finally {
      setBusy(false);
    }
  }

  function leaveInvite() {
    window.history.replaceState({}, "", window.location.pathname);
    setInvite(null); setPassword(""); setRepeat(""); setError(null); setInviteDead(false);
  }

  if (invite) {
    const mismatch = repeat.length > 0 && repeat !== password;
    return (
      <div className="vs-login">
        <div className="vs-card vs-login-card">
          <h1>Добро пожаловать</h1>
          {inviteDead ? (
            <>
              <p className="vs-muted">
                Ссылка недействительна: ею уже воспользовались или администратор
                выдал вместо неё новую. Если пароль уже задан — войдите с ним,
                если нет — попросите администратора прислать новую ссылку.
              </p>
              <button className="vs-btn primary block" onClick={leaveInvite}>
                Перейти ко входу
              </button>
            </>
          ) : (
            <>
              <p className="vs-muted vs-small">
                Придумайте пароль для входа в график отпусков. Не короче
                8 символов, не только из цифр и не похожий на имя или почту.
              </p>
              <label className="vs-field">
                Пароль
                <input type="password" value={password} autoComplete="new-password"
                  onChange={(e) => setPassword(e.target.value)} />
              </label>
              <label className="vs-field">
                Пароль ещё раз
                <input type="password" value={repeat} autoComplete="new-password"
                  onChange={(e) => setRepeat(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && !mismatch && password
                    && acceptInvite()} />
              </label>
              {mismatch && <div className="vs-muted vs-small">Пароли не совпадают</div>}
              <button className="vs-btn primary block" onClick={acceptInvite}
                disabled={busy || !password || repeat !== password}>
                {busy ? "Сохраняем…" : "Задать пароль и войти"}
              </button>
              {error && <div className="vs-issue"><div className="vs-issue-text">{error}</div></div>}
            </>
          )}
        </div>
      </div>
    );
  }

  const demo = methods?.demo.enabled ? methods.demo : null;

  if (demo && demoOpen) {
    return (
      <div className="vs-login">
        <div className="vs-card vs-login-card vs-demo-card">
          <h1>Демо-режим</h1>
          <p className="vs-muted">
            Вымышленная компания на 157 человек с готовым графиком на следующий
            год. Выберите, кем войти, — пароль не нужен.
          </p>

          <div className="vs-demo-roles">
            {demo.roles.map((role) => (
              <button key={role.code} className="vs-demo-role"
                onClick={() => demoLogin(role.code)} disabled={busy}>
                <span className="vs-demo-role-title">{role.title}</span>
                <span className="vs-demo-role-name">{role.full_name}</span>
                <span className="vs-demo-role-text">{role.description}</span>
              </button>
            ))}
          </div>

          <p className="vs-small vs-muted">
            Изменения в демо видят все его посетители. Данные сбрасываются
            при перезапуске сервиса и не связаны с рабочей системой.
          </p>

          {error && <div className="vs-issue"><div className="vs-issue-text">{error}</div></div>}

          <button className="vs-link" onClick={() => { setDemoOpen(false); setError(null); }}
            disabled={busy}>
            ← Обычный вход
          </button>
        </div>
      </div>
    );
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

        {demo && (
          <>
            <div className="vs-login-divider"><span>или</span></div>
            <button className="vs-btn block" onClick={() => { setDemoOpen(true); setError(null); }}
              disabled={busy}>
              Демо-режим
            </button>
            <p className="vs-small vs-muted vs-demo-hint">
              Посмотреть возможности на готовых данных, без регистрации
            </p>
          </>
        )}
      </div>
    </div>
  );
}
