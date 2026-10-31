import { useCallback, useEffect, useMemo, useState } from "react";
import { employeesAdminApi, isDemo } from "../api";
import type { Invitation } from "../types";

/** Ссылка ведёт на этот же интерфейс: сервер не знает, по какому адресу
    сотрудники открывают приложение. */
export function inviteLink(token: string) {
  return `${window.location.origin}${window.location.pathname}?invite=${token}`;
}

function csvCell(value: string) {
  return /[;"\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
}

/** Приглашения (релиз 1.1, п. 3): администратор выгружает список ссылок
    и рассылает его сам средствами компании. */
export default function Invitations({ reloadKey }: { reloadKey: number }) {
  const [rows, setRows] = useState<Invitation[] | null>(null);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRows(await employeesAdminApi.invitations());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить приглашения");
    }
  }, []);

  useEffect(() => { void load(); }, [load, reloadKey]);

  const visible = useMemo(() => {
    const query = filter.trim().toLowerCase();
    if (!rows || !query) return rows ?? [];
    return rows.filter((r) => [r.full_name, r.email, r.employee_number, r.org_unit ?? ""]
      .some((v) => v.toLowerCase().includes(query)));
  }, [rows, filter]);

  function flash(key: string) {
    setCopied(key);
    window.setTimeout(() => setCopied((c) => (c === key ? null : c)), 1500);
  }

  async function copy(text: string, key: string) {
    try {
      await navigator.clipboard.writeText(text);
      flash(key);
    } catch {
      setError("Браузер не дал скопировать. Скачайте список файлом.");
    }
  }

  function download() {
    const header = "Табельный номер;ФИО;Email;Подразделение;Ссылка";
    const lines = visible.map((r) => [r.employee_number, r.full_name, r.email,
      r.org_unit ?? "", inviteLink(r.invitation_token)].map(csvCell).join(";"));
    // BOM — чтобы Excel открыл кириллицу без вопросов о кодировке.
    const blob = new Blob(["﻿" + [header, ...lines].join("\r\n")],
      { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `приглашения-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function reissue(row: Invitation) {
    try {
      const fresh = await employeesAdminApi.reissueInvitation(row.employee_id);
      setRows((list) => (list ?? []).map((r) => (r.employee_id === fresh.employee_id ? fresh : r)));
      await copy(inviteLink(fresh.invitation_token), fresh.employee_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось выдать новую ссылку");
    }
  }

  return (
    <section className="vs-card vs-invites">
      <div className="vs-invites-head">
        <h2 className="vs-section-title">
          Приглашения {rows && <span className="vs-muted">{rows.length}</span>}
        </h2>
        {rows && rows.length > 0 && (
          <div className="vs-decision-actions">
            <button className="vs-btn" onClick={() => copy(
              visible.map((r) => `${r.full_name}\t${r.email}\t${inviteLink(r.invitation_token)}`)
                .join("\n"), "all")}>
              {copied === "all" ? "Скопировано" : "Скопировать список"}
            </button>
            <button className="vs-btn primary" onClick={download}>Скачать CSV</button>
          </div>
        )}
      </div>
      <p className="vs-muted vs-small">
        Здесь сотрудники, которые ещё не задали пароль. Разошлите каждому его ссылку
        почтой или в мессенджере: по ней сотрудник задаёт пароль и входит. После
        этого строка исчезает. Ссылка — ключ от учётной записи, не публикуйте
        список целиком в общих чатах.
      </p>

      {isDemo() && (
        <div className="vs-note">
          В демо-режиме ссылки не открываются: вход по ссылке ведёт в рабочую
          систему, а этих сотрудников там нет. Список показан для знакомства с экраном.
        </div>
      )}
      {error && <div className="vs-error vs-error-inline">{error}</div>}

      {rows === null ? <div className="vs-muted vs-small">Загружаем…</div>
        : rows.length === 0 ? (
          <div className="vs-muted vs-small">Все сотрудники уже задали пароль.</div>
        ) : (
          <>
            {rows.length > 10 && (
              <label className="vs-field vs-inline-field">
                Найти
                <input value={filter} placeholder="ФИО, почта, подразделение"
                  onChange={(e) => setFilter(e.target.value)} />
              </label>
            )}
            <table className="vs-table vs-invites-table">
              <tbody>
                {visible.map((row) => (
                  <tr key={row.employee_id}>
                    <td>
                      <div>{row.full_name}</div>
                      <div className="vs-muted vs-small">
                        {row.email}{row.org_unit ? ` · ${row.org_unit}` : ""}
                      </div>
                    </td>
                    <td className="vs-invites-actions">
                      <button className="vs-link"
                        onClick={() => copy(inviteLink(row.invitation_token), row.employee_id)}>
                        {copied === row.employee_id ? "Скопировано" : "Копировать ссылку"}
                      </button>
                      <button className="vs-link" onClick={() => reissue(row)}
                        title="Старая ссылка перестанет действовать">
                        Новая ссылка
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
    </section>
  );
}
