import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Notification } from "../types";

function ago(iso: string) {
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return "только что";
  if (minutes < 60) return `${minutes} мин назад`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} ч назад`;
  return new Date(iso).toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
}

/** Уведомления в шапке: счётчик непрочитанных и список последних.
    Раньше счётчик только рос — отметить прочитанным было негде. */
export default function NotificationBell({ items, unread, onChanged, onOpen }: {
  items: Notification[];
  unread: number;
  onChanged: () => void;
  /** Переход к месту, где по уведомлению нужно действовать. */
  onOpen: (note: Notification) => void;
}) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const outside = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", outside);
    window.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", outside);
      window.removeEventListener("keydown", esc);
    };
  }, [open]);

  async function read(note: Notification) {
    setOpen(false);
    onOpen(note);
    if (note.is_read) return;
    await api.markRead(note.id).catch(() => undefined);
    onChanged();
  }

  async function readAll() {
    await api.markAllRead().catch(() => undefined);
    onChanged();
  }

  const label = unread > 0 ? `Уведомления, непрочитанных: ${unread}` : "Уведомления";
  return (
    <div className="vs-bell-box" ref={box}>
      <button className="vs-bell" aria-label={label} title={label} aria-expanded={open}
        onClick={() => setOpen((v) => !v)}>
        <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" fill="none"
          stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
          <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
        </svg>
        {unread > 0 && <span className="vs-badge">{unread > 99 ? "99+" : unread}</span>}
      </button>
      {open && (
        <div className="vs-bell-panel" role="dialog" aria-label="Уведомления">
          <div className="vs-bell-head">
            <strong>Уведомления</strong>
            {unread > 0 && <button className="vs-link" onClick={readAll}>Прочитать все</button>}
          </div>
          {items.length === 0 ? (
            <div className="vs-muted vs-small vs-bell-empty">Уведомлений пока нет.</div>
          ) : (
            <ul className="vs-bell-list">
              {items.map((note) => (
                <li key={note.id}>
                  <button className={`vs-bell-item${note.is_read ? "" : " unread"}`}
                    onClick={() => read(note)}
                    title="Открыть">
                    <span className="vs-bell-text">{note.message}</span>
                    <span className="vs-muted vs-small">{ago(note.created_at)}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
