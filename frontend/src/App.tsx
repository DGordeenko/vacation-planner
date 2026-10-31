import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError, isDemo, managerApi, persistToken, restoreToken } from "./api";
import { fmt } from "./dates";
import Login from "./components/Login";
import SummaryPanel from "./components/SummaryPanel";
import YearCalendar from "./components/YearCalendar";
import { ExistingEntryPanel, SelectionPanel } from "./components/EntryPanel";
import HolidayAdmin from "./components/HolidayAdmin";
import Decisions from "./components/Decisions";
import TeamCalendar from "./components/TeamCalendar";
import EmployeeImport from "./components/EmployeeImport";
import AuditLog from "./components/AuditLog";
import Settings from "./components/Settings";
import TimeOff from "./components/TimeOff";
import TagsAdmin from "./components/TagsAdmin";
import type {
  AnnualSummary, Branding, Holiday, Me, Notification,
  ScheduleEntry, ValidationResult,
} from "./types";

const YEAR = new Date().getFullYear() + 1;

/** Контрастный цвет к фирменному — тот же расчёт, что на сервере при сохранении. */
function bestContrast(hex: string) {
  const channels = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  const luminance = 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
  return luminance > 0.45 ? "#16202b" : "#ffffff";
}

export default function App() {
  // Ссылка-приглашение открывает экран задания пароля, даже если в этом
  // браузере уже кто-то вошёл: иначе ссылка молча откроет чужой график.
  const [authorized, setAuthorized] = useState(
    () => Boolean(restoreToken()) && !new URLSearchParams(window.location.search).has("invite"));
  const [me, setMe] = useState<Me | null>(null);
  const [branding, setBranding] = useState<Branding | null>(null);
  const [summary, setSummary] = useState<AnnualSummary | null>(null);
  const [entries, setEntries] = useState<ScheduleEntry[]>([]);
  const [holidays, setHolidays] = useState<Map<string, Holiday>>(new Map());
  const [notifications, setNotifications] = useState<Notification[]>([]);

  const [anchor, setAnchor] = useState<string | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [selection, setSelection] = useState<{ from: string; to: string } | null>(null);
  const [check, setCheck] = useState<ValidationResult | null>(null);
  const [checking, setChecking] = useState(false);
  const [saving, setSaving] = useState(false);
  const [openEntry, setOpenEntry] = useState<ScheduleEntry | null>(null);
  const [activeTagId, setActiveTagId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState<
    "schedule" | "holidays" | "decisions" | "team" | "tags" | "employees" | "audit" | "settings" | "timeoff">("schedule");
  const [decisionsCount, setDecisionsCount] = useState(0);

  const reload = useCallback(async () => {
    if (!authorized) return;
    try {
      const profile = await api.me();
      setMe(profile);
      const [brand, days, sum, list, inbox] = await Promise.all([
        api.branding(),
        api.holidays(YEAR),
        api.summary(profile.id, YEAR),
        api.entries(profile.id, YEAR),
        api.notifications(),
      ]);
      setBranding(brand);
      setHolidays(new Map(days.map((d) => [d.date, d])));
      setSummary(sum);
      setEntries(list.results);
      setNotifications(inbox.results);
      // Счётчик «Требует моего решения» — в шапке, чтобы руководитель видел
      // задачи, не открывая экран (UX 2.4).
      if (profile.is_manager || profile.is_admin) {
        managerApi.decisions().then((d) => setDecisionsCount(d.count)).catch(() => undefined);
      }
      setError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        persistToken(null);
        setAuthorized(false);
      } else {
        setError(err instanceof Error ? err.message : "Не удалось загрузить данные");
      }
    } finally {
      setLoading(false);
    }
  }, [authorized]);

  useEffect(() => { void reload(); }, [reload]);

  /* Итог действия держится 10 секунд и закрывается крестиком. */
  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 10000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  /* Esc закрывает открытую панель. */
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setSelection(null); setSaveError(null); setOpenEntry(null);
      setAnchor(null); setHover(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  /* Проверка выполняется на сервере — той же логикой, что и сохранение. */
  useEffect(() => {
    if (!selection) { setCheck(null); return; }
    let cancelled = false;
    setChecking(true);
    api.validate(selection.from, selection.to)
      .then((result) => { if (!cancelled) setCheck(result); })
      .catch((err) => { if (!cancelled) setError(err.message); })
      .finally(() => { if (!cancelled) setChecking(false); });
    return () => { cancelled = true; };
  }, [selection]);

  const pending = useMemo(() => {
    if (!anchor) return null;
    const end = hover || anchor;
    return anchor <= end ? { from: anchor, to: end } : { from: end, to: anchor };
  }, [anchor, hover]);

  const activeTag = useMemo(
    () => summary?.tags.find((t) => t.tag_id === activeTagId) ?? null,
    [summary, activeTagId]
  );

  // В режиме правок календарь доступен: записи снова в черновике.
  const locked = (summary?.submitted ?? false) || (summary?.year_approved ?? false);
  const unread = notifications.filter((n) => !n.is_read).length;

  const dayClick = (key: string) => {
    if (locked) return;
    const existing = entries.find(
      (e) => key >= e.date_from && key <= (e.date_to || e.date_from)
    );
    if (existing && !anchor) {
      setOpenEntry(existing);
      setSelection(null);
      return;
    }
    if (!anchor) {
      setAnchor(key); setHover(key); setSelection(null); setOpenEntry(null);
      return;
    }
    setSelection(anchor <= key ? { from: anchor, to: key } : { from: key, to: anchor });
    setAnchor(null); setHover(null);
  };

  async function save() {
    if (!selection) return;
    setSaving(true);
    try {
      const extension = check?.holiday_extension;
      const to = extension && extension.added_days > 0
        ? extension.suggested_date_to : selection.to;
      const entry = await api.createEntry(selection.from, to);
      setSelection(null);
      setSaveError(null);
      const period = `${fmt(entry.date_from)} — ${fmt(entry.date_to || entry.date_from)}`;
      setNotice(entry.pending_exceptions.length > 0
        ? `Отпуск ${period} сохранён черновиком, запрос на подтверждение отправлен`
          + `${summary?.manager_name ? ` руководителю: ${summary.manager_name}` : " руководителю"}.`
          + " Отправить график можно после его решения."
        : `Отпуск ${period} сохранён.`);
      await reload();
    } catch (err) {
      // Ошибка — в самой панели: туда сейчас смотрит пользователь.
      setSaveError(err instanceof Error ? err.message : "Не удалось сохранить");
    } finally {
      setSaving(false);
    }
  }

  async function remove(id: string) {
    try {
      await api.deleteEntry(id);
      setOpenEntry(null);
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось удалить");
    }
  }

  async function submit() {
    if (!me) return;
    try {
      await api.submitYear(me.id, YEAR);
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось отправить");
    }
  }

  async function runAmendment(
    operation: (id: string) => Promise<unknown>, id: string | null
  ) {
    if (!id) return;
    try {
      await operation(id);
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось выполнить действие");
    }
  }

  async function openAmendment() {
    if (!me) return;
    try {
      await api.openAmendment(me.id, YEAR);
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось открыть правки");
    }
  }

  async function withdraw() {
    if (!summary?.approval_id) return;
    try {
      await api.withdraw(summary.approval_id);
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось отозвать");
    }
  }

  /* Выход сбрасывает и состояние экрана: иначе после смены пользователя
     (в демо — смены роли) осталось бы выделение и открытая панель прежнего. */
  function signOut() {
    persistToken(null);
    setAuthorized(false);
    setMe(null);
    setSummary(null);
    setEntries([]);
    setNotifications([]);
    setAnchor(null);
    setHover(null);
    setSelection(null);
    setOpenEntry(null);
    setActiveTagId(null);
    setError(null);
    setSaveError(null);
    setNotice(null);
    setDecisionsCount(0);
    setView("schedule");
  }

  if (!authorized) {
    return <Login onSuccess={() => { setAuthorized(true); setLoading(true); }} />;
  }
  if (loading || !me || !summary || !branding) {
    return <div className="vs-loading">Загружаем график…</div>;
  }

  const brand = branding.brand_primary_color;
  const contrast = branding.brand_contrast_color || bestContrast(brand);

  return (
    <div className={`vs-root${view === "schedule" && (selection || openEntry) ? " has-panel" : ""}`} style={{
      ["--brand" as string]: brand,
      ["--brand-contrast" as string]: contrast,
      ["--brand-radius" as string]: branding.brand_radius,
    }}>
      {isDemo() && (
        <div className="vs-demo-strip" role="status">
          <strong>Демо-режим</strong>
          <span>
            Данные вымышленные. Изменения видят все посетители демо,
            при перезапуске сервиса всё возвращается к исходному.
          </span>
          <button className="vs-link" onClick={signOut}>Сменить роль</button>
        </div>
      )}

      <header className="vs-bar">
        <div className="vs-mark">{branding.organization_name.slice(0, 1)}</div>
        <div>
          <div className="vs-org">{branding.organization_name}</div>
          <div className="vs-small vs-muted">График отпусков на {YEAR} год</div>
        </div>
        <div className="vs-right">
          {unread > 0 && <span className="vs-badge">{unread}</span>}
          <nav className="vs-nav" aria-label="Разделы">
            <button className="vs-nav-item" aria-current={view === "schedule"}
              onClick={() => setView("schedule")}>Мой график</button>
            <button className="vs-nav-item" aria-current={view === "timeoff"}
              onClick={() => setView("timeoff")}>Отгулы</button>
            {(me.is_manager || me.is_admin) && (
              <>
                <button className="vs-nav-item" aria-current={view === "decisions"}
                  onClick={() => setView("decisions")}>
                  Требует решения
                  {decisionsCount > 0 && <span className="vs-badge">{decisionsCount}</span>}
                </button>
                <button className="vs-nav-item" aria-current={view === "team"}
                  onClick={() => setView("team")}>Команда</button>
                <button className="vs-nav-item" aria-current={view === "tags"}
                  onClick={() => setView("tags")}>Теги</button>
              </>
            )}
            {me.is_admin && (
              <>
                <button className="vs-nav-item" aria-current={view === "employees"}
                  onClick={() => setView("employees")}>Сотрудники</button>
                <button className="vs-nav-item" aria-current={view === "holidays"}
                  onClick={() => setView("holidays")}>Праздники</button>
                <button className="vs-nav-item" aria-current={view === "audit"}
                  onClick={() => setView("audit")}>Журнал</button>
                <button className="vs-nav-item" aria-current={view === "settings"}
                  onClick={() => setView("settings")}>Настройки</button>
              </>
            )}
          </nav>
          <span className="vs-muted vs-small">{me.full_name}</span>
          <button className="vs-link" onClick={signOut}>Выйти</button>
        </div>
      </header>

      {error && (
        <div className="vs-error">
          {error}
          <button className="vs-link" onClick={() => setError(null)}>скрыть</button>
        </div>
      )}

      {view === "holidays" ? (
        <HolidayAdmin onBack={() => { setView("schedule"); void reload(); }} />
      ) : view === "timeoff" ? (
        <TimeOff me={me} onBack={() => { setView("schedule"); void reload(); }}
          onChanged={() => void reload()} />
      ) : view === "settings" ? (
        <Settings branding={branding} onBack={() => { setView("schedule"); void reload(); }}
          onBrandingChanged={() => void reload()} />
      ) : view === "audit" ? (
        <AuditLog onBack={() => setView("schedule")} />
      ) : view === "employees" ? (
        <EmployeeImport onBack={() => { setView("schedule"); void reload(); }} />
      ) : view === "decisions" ? (
        <Decisions onBack={() => { setView("schedule"); void reload(); }}
          onChanged={() => void reload()} />
      ) : view === "team" ? (
        <TeamCalendar year={YEAR} onBack={() => setView("schedule")} />
      ) : view === "tags" ? (
        <TagsAdmin canEdit={me.is_admin}
          onBack={() => { setView("schedule"); void reload(); }} />
      ) : (
      <div className="vs-shell">
        <aside className="vs-rail">
          <SummaryPanel summary={summary} activeTag={activeTagId}
            onTagToggle={setActiveTagId} />

          <div className="vs-card">
            <h2>Внесённые отпуска</h2>
            {entries.length === 0 ? (
              <p className="vs-muted vs-small">
                Пока ничего не запланировано. Выберите даты в календаре.
              </p>
            ) : (
              <ul className="vs-list">
                {entries.map((entry) => (
                  <li className="vs-item" key={entry.id}>
                    <span className="vs-item-dates">
                      {fmt(entry.date_from)} — {fmt(entry.date_to || entry.date_from)}
                    </span>
                    <span className="vs-item-days">{entry.vacation_days} дн.</span>
                    {entry.status === "rejected" && (
                      <span className="vs-item-flag rejected"
                        title={entry.rejection_comment}>не подтверждён</span>
                    )}
                    {entry.pending_exceptions.length > 0 && (
                      <span className="vs-item-flag" title={entry.pending_exceptions
                        .map((v) => v.name).join(", ")}>ждёт подтверждения</span>
                    )}
                    {!locked && (entry.status === "draft" || entry.status === "rejected") && (
                      <button className="vs-x" onClick={() => remove(entry.id)}
                        aria-label="Удалить отпуск">✕</button>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>

          {notifications.length > 0 && (
            <div className="vs-card">
              <h2>Уведомления</h2>
              <ul className="vs-list">
                {notifications.slice(0, 5).map((note) => (
                  <li className="vs-item vs-note-item" key={note.id}>
                    <span className="vs-small">{note.message}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </aside>

        <main>
          {summary.year_approved ? (
            <div className="vs-banner">
              <strong>График на {YEAR} год утверждён</strong>
              <span className="vs-muted">
                Изменения потребуют согласования двух уровней руководства.
              </span>
              <button className="vs-btn" onClick={openAmendment}>
                Внести изменения
              </button>
            </div>
          ) : summary.amendment_status === "open" ? (
            <div className="vs-banner" style={{ borderLeftColor: "var(--warn)" }}>
              <strong>Правка утверждённого графика</strong>
              <span className="vs-muted">
                Изменения вступят в силу после согласования. Отмена вернёт
                утверждённый график.
              </span>
              <div className="vs-banner-actions">
                <button className="vs-btn"
                  onClick={() => runAmendment(api.cancelAmendment, summary.amendment_id)}>
                  Отменить правки
                </button>
                <button className="vs-btn primary"
                  onClick={() => runAmendment(api.submitAmendment, summary.amendment_id)}>
                  Отправить на согласование
                </button>
              </div>
            </div>
          ) : locked ? (
            <div className="vs-banner vs-banner-column">
              <div className="vs-banner-row">
                <strong>
                  {summary.amendment_status === "pending"
                    ? "Правки на согласовании" : "График на согласовании"}
                </strong>
                <span className="vs-muted">Правки заблокированы до решения.</span>
                <button className="vs-btn" onClick={withdraw}>Отозвать график</button>
              </div>
              {summary.approval_steps.length > 0 && (
                <ol className="vs-chain">
                  {summary.approval_steps.map((step) => (
                    <li key={step.order} className={`vs-chain-step ${step.status}`}>
                      <span className="vs-chain-mark" aria-hidden="true">
                        {step.status === "approved" ? "✓"
                          : step.status === "rejected" ? "✕" : step.order}
                      </span>
                      <span className="vs-chain-body">
                        <span className="vs-chain-name">{step.approver_name}</span>
                        <span className="vs-chain-level">{step.level_display}</span>
                        {step.comment && (
                          <span className="vs-chain-comment">{step.comment}</span>
                        )}
                      </span>
                    </li>
                  ))}
                </ol>
              )}
            </div>
          ) : (
            <div className="vs-top">
              <p className="vs-muted vs-small">
                {anchor ? "Выберите день окончания отпуска."
                  : "Выберите день начала, затем день окончания."}
              </p>
              <button className="vs-btn primary" onClick={submit}
                disabled={entries.length === 0}>
                Отправить руководителю
              </button>
            </div>
          )}

          <YearCalendar year={YEAR} entries={entries} holidays={holidays}
            activeTag={activeTag} pending={pending} locked={locked}
            onDayClick={dayClick}
            onDayHover={(key) => anchor && setHover(key)} />
        </main>
      </div>
      )}

      {view === "schedule" && selection && (
        <SelectionPanel selection={selection} check={check} checking={checking}
          saving={saving} saveError={saveError} managerName={summary.manager_name}
          onCancel={() => { setSelection(null); setSaveError(null); }} onSave={save} />
      )}

      {view === "schedule" && openEntry && !selection && (
        <ExistingEntryPanel entry={openEntry} onClose={() => setOpenEntry(null)}
          onDelete={() => remove(openEntry.id)} managerName={summary.manager_name} />
      )}

      {notice && (
        <div className="vs-toast" role="status">
          <span>{notice}</span>
          <button className="vs-x" onClick={() => setNotice(null)} aria-label="Закрыть">✕</button>
        </div>
      )}
    </div>
  );
}
