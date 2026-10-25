import { useEffect, useState } from "react";
import { managerApi } from "../api";
import { fmt } from "../dates";
import type { TeamEntryDetail } from "../types";

const STATUS_TITLE: Record<string, string> = {
  approved: "Утверждён",
  pending_approval: "На согласовании",
  draft: "Черновик",
};

/** Карточка отпуска из календаря команды: у кого он сейчас на решении.
    Цепочку и видимость собирает сервер — карточка только показывает. */
export default function TeamEntryCard({ id, onClose }: { id: string; onClose: () => void }) {
  const [data, setData] = useState<TeamEntryDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setData(null); setError(null);
    managerApi.teamEntry(id).then(setData)
      .catch((err) => setError(err instanceof Error ? err.message : "Не удалось загрузить"));
  }, [id]);

  useEffect(() => {
    const close = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [onClose]);

  const current = data?.steps.find((s) => s.is_current);
  const what = data?.record_type === "day_off" ? "Отгул" : "Отпуск";

  return (
    <div className="vs-panel" role="dialog" aria-label="Отпуск сотрудника">
      <div className="vs-panel-head">
        <span className="vs-chain-body">
          <strong>{data ? data.employee_name : "Загружаем…"}</strong>
          {data?.org_unit && <span className="vs-muted vs-small">{data.org_unit}</span>}
        </span>
        <button className="vs-link vs-panel-close" onClick={onClose}
          aria-label="Закрыть">✕</button>
      </div>
      <div className="vs-panel-body">
        {error && <div className="vs-issue hard"><div className="vs-issue-text">{error}</div></div>}
        {data && <>
          <dl className="vs-rows">
            <div className="vs-row">
              <dt>{what}</dt>
              <dd>{fmt(data.date_from)} — {fmt(data.date_to)}
                {data.vacation_days != null && ` · ${data.vacation_days} дн.`}</dd>
            </div>
            <div className="vs-row">
              <dt>Статус</dt>
              <dd>{STATUS_TITLE[data.status] ?? data.status}</dd>
            </div>
            {current && (
              <div className="vs-row">
                <dt>Ждёт решения</dt>
                <dd><strong>{current.approver_name}</strong></dd>
              </div>
            )}
          </dl>

          {data.steps.length > 0 && (
            <ol className="vs-chain vs-chain-vertical" aria-label="Цепочка согласования">
              {data.steps.map((step) => (
                <li key={step.id}
                  className={`vs-chain-step ${step.status}${step.is_current ? " current" : ""}`}>
                  <span className="vs-chain-mark" aria-hidden="true">
                    {step.status === "approved" ? "✓" : step.status === "rejected" ? "✕" : step.order}
                  </span>
                  <span className="vs-chain-body">
                    <span className="vs-chain-name">{step.approver_name}</span>
                    <span className="vs-chain-level">
                      {step.level_display}
                      {step.status === "approved" && step.decided_at
                        && ` · согласовал ${fmt(step.decided_at.slice(0, 10))}`}
                      {step.is_current && " · решение за ним"}
                      {step.status === "pending" && !step.is_current && " · ждёт очереди"}
                    </span>
                    {step.comment && <span className="vs-chain-comment">{step.comment}</span>}
                  </span>
                </li>
              ))}
            </ol>
          )}

          {data.exception && (
            <div className="vs-issue">
              <div className="vs-issue-name">
                Ждёт подтверждения исключения: {data.exception.approver_name}
              </div>
              {data.exception.violations.map((v) => (
                <div key={v.code} className="vs-issue-text">{v.message}</div>
              ))}
              <div className="vs-issue-text vs-muted">
                Пока руководитель не решит, сотрудник не может отправить график.
              </div>
            </div>
          )}

          {data.status === "draft" && !data.exception && (
            <p className="vs-muted vs-small">Черновик: сотрудник ещё не отправил график.</p>
          )}
          {data.status === "approved" && (
            <p className="vs-muted vs-small">Отпуск утверждён всей цепочкой.</p>
          )}
        </>}
      </div>
    </div>
  );
}
