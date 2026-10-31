import { useEffect, useState } from "react";
import { ApiError, settingsApi, tagsApi } from "../api";
import type {
  Branding, CompanySettingsRow, EmployeeBrief, RvdTrackingOverrides, ValidationRuleRow,
} from "../types";

/** Ошибки полей из ответа сервера: и вида {errors: {...}}, и DRF {field: [...]}. */
function fieldErrors(err: unknown): Record<string, string> {
  if (!(err instanceof ApiError)) return {};
  const body = (err.payload ?? {}) as Record<string, unknown>;
  const source = (body.errors ?? body) as Record<string, unknown>;
  const result: Record<string, string> = {};
  Object.entries(source).forEach(([key, value]) => {
    if (Array.isArray(value)) result[key] = value.join(" ");
  });
  return result;
}

function message(err: unknown) {
  const fields = Object.values(fieldErrors(err));
  if (fields.length) return fields.join(" ");
  return err instanceof Error ? err.message : "Не удалось сохранить";
}

/** Настройки (релиз 1.2): нормы и расчёты, правила проверки, оформление.
    Всё, что меняет расчёты, проверяет и применяет сервер — здесь только ввод. */
export default function Settings({ branding, onBack, onBrandingChanged }: {
  branding: Branding;
  onBack: () => void;
  onBrandingChanged: () => void;
}) {
  return (
    <div className="vs-admin">
      <div className="vs-top">
        <button className="vs-link" onClick={onBack}>← К моему графику</button>
        <h1 className="vs-admin-title">Настройки</h1>
      </div>
      <div className="vs-settings">
        <CompanySection />
        <RvdTrackingSection />
        <RulesSection />
        <BrandingSection branding={branding} onChanged={onBrandingChanged} />
      </div>
    </div>
  );
}

const COMPANY_FIELDS: {
  key: keyof Omit<CompanySettingsRow, "rvd_compensation_tracking_enabled">;
  label: string;
  hint: string;
  step: string;
}[] = [
  { key: "annual_vacation_norm_days", label: "Право на отпуск в год, дн.", step: "1",
    hint: "Право сотрудника на год по умолчанию (ст. 115 ТК РФ — 28). Своя норма у отдельных сотрудников задаётся в карточке." },
  { key: "monthly_accrual_rate", label: "Начисление в месяц, дн.", step: "0.01",
    hint: "Обычно норма / 12: 28 → 2,33. Баланс считается заново из истории, поэтому новое значение меняет остатки всех сотрудников задним числом." },
  { key: "approval_levels", label: "Уровней согласования", step: "1",
    hint: "Сколько уровней руководства согласуют график (1–5). Действует для графиков, отправленных после изменения." },
  { key: "workday_hours", label: "Длина рабочего дня, ч", step: "0.1",
    hint: "Столько часов списывает один отгул." },
];

function CompanySection() {
  const [saved, setSaved] = useState<CompanySettingsRow | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [rvd, setRvd] = useState(true);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function fill(row: CompanySettingsRow) {
    setSaved(row);
    setDraft(Object.fromEntries(COMPANY_FIELDS.map((f) => [f.key, String(row[f.key])])));
    setRvd(row.rvd_compensation_tracking_enabled);
  }

  useEffect(() => {
    settingsApi.get().then(fill).catch((err) => setStatus(message(err)));
  }, []);

  if (!saved) return <section className="vs-card">{status ?? "Загружаем…"}</section>;

  const changed = COMPANY_FIELDS.filter((f) => Number(draft[f.key]) !== saved[f.key])
    .map((f) => f.key);
  const dirty = changed.length > 0 || rvd !== saved.rvd_compensation_tracking_enabled;

  async function save() {
    if (!saved) return;
    if (changed.includes("monthly_accrual_rate") && !window.confirm(
      "Новая ставка начисления пересчитает остатки отпуска всех сотрудников задним числом. Продолжить?")) {
      return;
    }
    setBusy(true); setErrors({}); setStatus(null);
    try {
      const patch: Record<string, unknown> = { rvd_compensation_tracking_enabled: rvd };
      changed.forEach((key) => { patch[key] = draft[key]; });
      fill(await settingsApi.save(patch as Partial<CompanySettingsRow>));
      setStatus("Сохранено");
    } catch (err) {
      setErrors(fieldErrors(err));
      if (!Object.keys(fieldErrors(err)).length) setStatus(message(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="vs-card vs-settings-section">
      <h2 className="vs-section-title">Нормы и расчёты</h2>
      <div className="vs-settings-grid">
        {COMPANY_FIELDS.map((f) => (
          <label key={f.key} className="vs-field">
            {f.label}
            <input type="number" step={f.step} value={draft[f.key] ?? ""}
              aria-invalid={Boolean(errors[f.key])}
              onChange={(e) => setDraft((d) => ({ ...d, [f.key]: e.target.value }))} />
            {errors[f.key]
              ? <span className="vs-field-error">{errors[f.key]}</span>
              : <span className="vs-muted vs-small">{f.hint}</span>}
          </label>
        ))}
      </div>
      <label className="vs-check">
        <input type="checkbox" checked={rvd} onChange={(e) => setRvd(e.target.checked)} />
        Учитывать компенсацию работы в выходной (отгул или двойная оплата)
      </label>
      <div className="vs-settings-foot">
        {status && <span className="vs-muted vs-small">{status}</span>}
        <button className="vs-btn" disabled={!dirty || busy} onClick={() => fill(saved)}>
          Отменить
        </button>
        <button className="vs-btn primary" disabled={!dirty || busy} onClick={save}>
          {busy ? "Сохраняем…" : "Сохранить"}
        </button>
      </div>
    </section>
  );
}

function RulesSection() {
  const [rules, setRules] = useState<ValidationRuleRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    settingsApi.rules().then((page) => setRules(page.results))
      .catch((err) => setError(message(err)));
  }, []);

  function replace(row: ValidationRuleRow) {
    setRules((list) => (list ?? []).map((r) => (r.code === row.code ? row : r)));
  }

  return (
    <section className="vs-card vs-settings-section">
      <h2 className="vs-section-title">Правила проверки графика</h2>
      <p className="vs-muted vs-small">
        Пороги — данные, а не код: новое значение сразу действует при внесении
        и проверке отпусков. Уже внесённые записи не пересчитываются, пока
        их не изменят или не отправят на согласование.
      </p>
      {error && <div className="vs-error vs-error-inline">{error}</div>}
      {rules === null ? <div className="vs-muted vs-small">Загружаем…</div> : (
        <div className="vs-rules">
          {rules.map((rule) => <RuleLine key={rule.code} rule={rule} onSaved={replace} />)}
        </div>
      )}
    </section>
  );
}

function RuleLine({ rule, onSaved }: {
  rule: ValidationRuleRow;
  onSaved: (row: ValidationRuleRow) => void;
}) {
  const [value, setValue] = useState(rule.threshold_value === null ? "" : String(Number(rule.threshold_value)));
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  async function patch(data: Partial<ValidationRuleRow>) {
    setError(null);
    try {
      const row = await settingsApi.saveRule(rule.code, data);
      onSaved(row);
      setSaved(true);
      window.setTimeout(() => setSaved(false), 1500);
    } catch (err) {
      setError(message(err));
      setValue(rule.threshold_value === null ? "" : String(Number(rule.threshold_value)));
    }
  }

  function commit() {
    if (rule.threshold_value === null) return;
    if (Number(value) === Number(rule.threshold_value) && value !== "") return;
    void patch({ threshold_value: value === "" ? null : value });
  }

  return (
    <div className={`vs-rule${rule.is_enabled ? "" : " off"}`}>
      <label className="vs-check vs-rule-switch">
        <input type="checkbox" checked={rule.is_enabled}
          onChange={(e) => void patch({ is_enabled: e.target.checked })} />
        <span>
          <span className="vs-rule-name">{rule.name}</span>
          {rule.description && <span className="vs-muted vs-small vs-rule-desc">{rule.description}</span>}
        </span>
      </label>
      <div className="vs-rule-threshold">
        {rule.threshold_value === null ? (
          <span className="vs-muted vs-small">без порога</span>
        ) : (
          <input type="number" step="1" min="0" value={value} aria-label={`Порог: ${rule.name}`}
            disabled={!rule.is_enabled}
            onChange={(e) => setValue(e.target.value)} onBlur={commit}
            onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} />
        )}
        {saved && <span className="vs-saved">сохранено</span>}
      </div>
      {error && <div className="vs-field-error vs-rule-error">{error}</div>}
    </div>
  );
}

function BrandingSection({ branding, onChanged }: { branding: Branding; onChanged: () => void }) {
  const [name, setName] = useState(branding.organization_name);
  const [colour, setColour] = useState(branding.brand_primary_color);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const dirty = name !== branding.organization_name
    || colour.toLowerCase() !== branding.brand_primary_color.toLowerCase();

  async function save() {
    setBusy(true); setErrors({}); setStatus(null);
    try {
      await settingsApi.saveBranding({ organization_name: name, brand_primary_color: colour });
      setStatus("Сохранено");
      onChanged();
    } catch (err) {
      setErrors(fieldErrors(err));
      if (!Object.keys(fieldErrors(err)).length) setStatus(message(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="vs-card vs-settings-section">
      <h2 className="vs-section-title">Оформление</h2>
      <div className="vs-settings-grid">
        <label className="vs-field">
          Название организации
          <input value={name} onChange={(e) => setName(e.target.value)}
            aria-invalid={Boolean(errors.organization_name)} />
          {errors.organization_name
            ? <span className="vs-field-error">{errors.organization_name}</span>
            : <span className="vs-muted vs-small">В шапке приложения и в письмах.</span>}
        </label>
        <label className="vs-field">
          Основной цвет
          <span className="vs-colour">
            <input type="color" value={colour} onChange={(e) => setColour(e.target.value)} />
            <input value={colour} onChange={(e) => setColour(e.target.value)} maxLength={7} />
            <span className="vs-colour-sample" style={{ background: colour }}>Кнопка</span>
          </span>
          {errors.brand_primary_color
            ? <span className="vs-field-error">{errors.brand_primary_color}</span>
            : <span className="vs-muted vs-small">Кнопки и выделения. Слишком светлый цвет сервер не примет: надписи станут нечитаемы.</span>}
        </label>
      </div>
      <p className="vs-muted vs-small">Логотип и шрифт пока задаются в админке Django.</p>
      <div className="vs-settings-foot">
        {status && <span className="vs-muted vs-small">{status}</span>}
        <button className="vs-btn primary" disabled={!dirty || busy} onClick={save}>
          {busy ? "Сохраняем…" : "Сохранить"}
        </button>
      </div>
    </section>
  );
}

function choice(value: boolean | null) {
  return value === null ? "" : value ? "on" : "off";
}

function parseChoice(raw: string): boolean | null {
  return raw === "" ? null : raw === "on";
}

/** Учёт компенсации РВД: частное важнее общего — сотрудник, затем
    подразделение вверх по структуре, затем компания. */
function RvdTrackingSection() {
  const [data, setData] = useState<RvdTrackingOverrides | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [unitId, setUnitId] = useState("");
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<EmployeeBrief[]>([]);

  useEffect(() => {
    settingsApi.rvdTracking().then(setData).catch((err) => setError(message(err)));
  }, []);

  useEffect(() => {
    if (query.trim().length < 2) { setFound([]); return; }
    const timer = window.setTimeout(() => {
      tagsApi.searchEmployees(query.trim()).then(setFound).catch(() => setFound([]));
    }, 250);
    return () => window.clearTimeout(timer);
  }, [query]);

  async function set(kind: "unit" | "employee", id: string, value: boolean | null) {
    setError(null);
    try {
      setData(await settingsApi.setRvdTracking(kind, id, value));
    } catch (err) {
      setError(message(err));
    }
  }

  if (!data) return <section className="vs-card">{error ?? "Загружаем…"}</section>;
  const overridden = data.units.filter((u) => u.value !== null);
  const free = data.units.filter((u) => u.value === null);

  return (
    <section className="vs-card vs-settings-section">
      <h2 className="vs-section-title">Учёт компенсации РВД: исключения</h2>
      <p className="vs-muted vs-small">
        Общая настройка компании — «{data.company ? "учитывать" : "не учитывать"}» (галочка выше).
        Подразделению или сотруднику можно задать иначе: настройка сотрудника важнее
        подразделения, подразделение — важнее вышестоящего и компании. Когда учёт
        выключен, при вводе РВД вид компенсации не спрашивается и часы отгула
        не начисляются.
      </p>
      {error && <div className="vs-error vs-error-inline">{error}</div>}

      {(overridden.length > 0 || data.employees.length > 0) && (
        <div className="vs-table-scroll">
        <table className="vs-table">
          <tbody>
            {overridden.map((u) => (
              <tr key={u.id}>
                <td>{u.path}<div className="vs-muted vs-small">подразделение</div></td>
                <td className="vs-override-cell">
                  <OverrideSelect value={u.value} onChange={(v) => set("unit", u.id, v)} />
                </td>
              </tr>
            ))}
            {data.employees.map((e) => (
              <tr key={e.id}>
                <td>{e.name}<div className="vs-muted vs-small">сотрудник{e.org_unit ? ` · ${e.org_unit}` : ""}</div></td>
                <td className="vs-override-cell">
                  <OverrideSelect value={e.value} onChange={(v) => set("employee", e.id, v)} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}

      <div className="vs-timeoff-form">
        <label className="vs-field vs-override-pick">
          Добавить подразделение
          <select value={unitId} onChange={(e) => setUnitId(e.target.value)}>
            <option value="">Выберите…</option>
            {free.map((u) => <option key={u.id} value={u.id}>{u.path}</option>)}
          </select>
        </label>
        <button className="vs-btn" disabled={!unitId}
          onClick={() => { void set("unit", unitId, !data.company); setUnitId(""); }}>
          {data.company ? "Не учитывать" : "Учитывать"}
        </button>
        <div className="vs-field vs-audit-person">
          Добавить сотрудника
          <input value={query} placeholder="ФИО или табельный" onChange={(e) => setQuery(e.target.value)} />
          {found.length > 0 && (
            <ul className="vs-list vs-found vs-audit-found">
              {found.slice(0, 8).map((e) => (
                <li key={e.id}>
                  <button className="vs-item vs-found-item" onClick={() => {
                    void set("employee", e.id, !data.company); setQuery(""); setFound([]);
                  }}>
                    <span>{e.full_name}</span>
                    <span className="vs-muted vs-small">{e.org_unit ?? ""}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
}

function OverrideSelect({ value, onChange }: {
  value: boolean | null;
  onChange: (value: boolean | null) => void;
}) {
  return (
    <select value={choice(value)} onChange={(e) => onChange(parseChoice(e.target.value))}
      aria-label="Учёт компенсации">
      <option value="on">Учитывать</option>
      <option value="off">Не учитывать</option>
      <option value="">Как выше (убрать исключение)</option>
    </select>
  );
}
