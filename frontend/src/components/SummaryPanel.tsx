import type { AnnualSummary } from "../types";

interface Props {
  summary: AnnualSummary;
  activeTag: string | null;
  onTagToggle: (tagId: string | null) => void;
}

function Metric({ label, value, target, hint, tone }: {
  label: string; value: number; target: number; hint: string;
  tone: "brand" | "ink";
}) {
  const share = target > 0 ? Math.min(100, (value / target) * 100) : 0;
  const short = value < target;
  return (
    <div className="vs-metric">
      <div className="vs-metric-top">
        <span>{label}</span>
        <span><strong>{value}</strong> из {target}</span>
      </div>
      <div className="vs-track">
        <div className={`vs-fill ${tone}${short && tone === "brand" ? " short" : ""}`}
          style={{ width: `${share}%` }} />
      </div>
      <div className={short && tone === "brand" ? "vs-need" : "vs-ok"}>{hint}</div>
    </div>
  );
}

export default function SummaryPanel({ summary, activeTag, onTagToggle }: Props) {
  const normDone = summary.against_norm >= summary.annual_norm;
  const continuousDone = summary.longest_continuous >= summary.min_continuous_required;

  return (
    <>
      <div className="vs-card">
        <h2>Накоплено дней</h2>
        <div className="vs-figure">{summary.accumulated_stock_exact.toFixed(1)}</div>
        <div className="vs-unit">на 1 января {summary.year}</div>
      </div>

      <div className="vs-card">
        <h2>Готовность графика</h2>

        <Metric label="Годовая норма" tone="brand"
          value={summary.against_norm} target={summary.annual_norm}
          hint={normDone ? "Норма закрыта"
            : `Запланируйте ещё ${summary.annual_norm - summary.against_norm} дней`} />

        <Metric label="Непрерывный отпуск" tone="brand"
          value={summary.longest_continuous} target={summary.min_continuous_required}
          hint={continuousDone ? "Требование выполнено"
            : `Нужен отпуск на ${summary.min_continuous_required} дней подряд`} />

        <Metric label="Из накопленных" tone="ink"
          value={summary.from_accumulated} target={Math.max(1, summary.accumulated_stock)}
          hint={summary.remaining < 0
            ? `Превышение на ${-summary.remaining} дней`
            : `Всего доступно ${summary.ceiling} дней, осталось ${summary.remaining}`} />
      </div>

      <div className="vs-card">
        <h2>Проверить пересечения</h2>
        <div className="vs-tags">
          {summary.tags.map((tag) => (
            <button key={tag.tag_id} className="vs-tag"
              aria-pressed={activeTag === tag.tag_id}
              onClick={() => onTagToggle(activeTag === tag.tag_id ? null : tag.tag_id)}>
              {tag.name}
              {tag.limit !== null && <span className="vs-tag-limit">{tag.limit}</span>}
            </button>
          ))}
        </div>
        <p className="vs-muted vs-small">
          {activeTag
            ? "Штриховка — дни, когда лимит по тегу исчерпан коллегами."
            : summary.tags.length > 0
              ? "Здесь только ваши теги: "
                + summary.tags.map((tag) => `«${tag.name}» — ${tag.source === "auto_org"
                  ? "по подразделению" : "назначен администратором"}`).join(", ")
                + ". Лимиты чужих тегов вас не ограничивают."
              : "У вас нет тегов — лимиты одновременного отсутствия вас не ограничивают."}
        </p>
      </div>
    </>
  );
}
