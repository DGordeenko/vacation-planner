import { useEffect, useState } from "react";
import { settingsApi } from "../api";
import { errorText, fieldErrors, plural } from "../text";
import EmployeePicker from "./EmployeePicker";
import { HistoryLines, MonthPicker, thisMonth } from "./SettingHistory";
import type {
  Branding, CompanySettingsRow, EmployeeBrief, HrViewer, RvdTrackingOverrides, VacationNormOverrides,
  ValidationRuleRow,
} from "../types";

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
        <VacationNormSection />
        <HrViewersSection />
        <RvdTrackingSection />
        <RulesSection />
        <BrandingSection branding={branding} onChanged={onBrandingChanged} />
      </div>
    </div>
  );
}

const COMPANY_FIELDS: {
  key: keyof Omit<CompanySettingsRow,
    "rvd_compensation_tracking_enabled" | "overtime_limits_enabled" | "history">;
  label: string;
  hint: string;
  step: string;
}[] = [
  { key: "annual_vacation_norm_days", label: "Право на отпуск в год, дн.", step: "1",
    hint: "Право сотрудника на год по умолчанию (ст. 115 ТК РФ — 28). Своя норма у отдельных сотрудников — в блоке «Своя норма отпуска» ниже." },
  { key: "monthly_accrual_rate", label: "Начисление в месяц, дн.", step: "0.01",
    hint: "Обычно норма / 12: 28 → 2,33. Начисления до выбранного месяца не меняются." },
  { key: "approval_levels", label: "Уровней согласования", step: "1",
    hint: "Сколько уровней руководства согласуют график (1–5). Действует для графиков, отправленных после изменения." },
  { key: "workday_hours", label: "Длина рабочего дня, ч", step: "0.1",
    hint: "Столько часов списывает один отгул." },
  { key: "overtime_limit_two_days_hours", label: "Сверхурочные за два дня подряд, ч", step: "0.5",
    hint: "Предел ст. 99 ТК РФ — 4 часа. Проверяется, если включён контроль ниже." },
  { key: "overtime_limit_year_hours", label: "Сверхурочные за год, ч", step: "1",
    hint: "Предел ст. 99 ТК РФ — 120 часов." },
];

function CompanySection() {
  const [saved, setSaved] = useState<CompanySettingsRow | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [rvd, setRvd] = useState(true);
  const [limits, setLimits] = useState(false);
  const [from, setFrom] = useState(thisMonth());
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function fill(row: CompanySettingsRow) {
    setSaved(row);
    setDraft(Object.fromEntries(COMPANY_FIELDS.map((f) => [f.key, String(row[f.key])])));
    setRvd(row.rvd_compensation_tracking_enabled);
    setLimits(row.overtime_limits_enabled);
  }

  useEffect(() => {
    settingsApi.get().then(fill).catch((err) => setStatus(errorText(err)));
  }, []);

  if (!saved) return <section className="vs-card">{status ?? "Загружаем…"}</section>;

  const changed = COMPANY_FIELDS.filter((f) => Number(draft[f.key]) !== saved[f.key])
    .map((f) => f.key);
  const dirty = changed.length > 0 || rvd !== saved.rvd_compensation_tracking_enabled
    || limits !== saved.overtime_limits_enabled;
  const dated = changed.some((key) => key in saved.history);

  async function removeDated(key: string, month: string) {
    if (!window.confirm("Отменить это изменение? С этого месяца снова будет действовать предыдущее значение.")) return;
    try {
      fill(await settingsApi.removeHistory<CompanySettingsRow>({ key, effective_from: month }));
    } catch (err) {
      setStatus(errorText(err));
    }
  }

  async function save() {
    if (!saved) return;
    if (dated && from < thisMonth() && !window.confirm(
      "Изменение задним числом пересчитает остатки и нормы сотрудников начиная с выбранного месяца. Продолжить?")) {
      return;
    }
    setBusy(true); setErrors({}); setStatus(null);
    try {
      const patch: Record<string, unknown> = {
        rvd_compensation_tracking_enabled: rvd, overtime_limits_enabled: limits,
        effective_from: from,
      };
      changed.forEach((key) => { patch[key] = draft[key]; });
      fill(await settingsApi.save(patch as Partial<CompanySettingsRow>));
      setStatus("Сохранено");
    } catch (err) {
      setErrors(fieldErrors(err));
      if (!Object.keys(fieldErrors(err)).length) setStatus(errorText(err));
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
            {f.key in saved.history && (
              <HistoryLines rows={saved.history[f.key as keyof CompanySettingsRow["history"]]}
                format={(v) => String(v ?? "").replace(".", ",")}
                onRemove={(month) => void removeDated(f.key, month)} />
            )}
          </label>
        ))}
      </div>
      {dated && (
        <div className="vs-dated">
          <MonthPicker label="Новые значения действуют с" value={from} onChange={setFrom} />
          <span className="vs-muted vs-small">
            Норма, начисление и длина рабочего дня меняются с первого числа месяца:
            всё, что было раньше, считается по прежним значениям.
          </span>
        </div>
      )}
      <label className="vs-check">
        <input type="checkbox" checked={rvd} onChange={(e) => setRvd(e.target.checked)} />
        Учитывать компенсацию работы в выходной (отгул или двойная оплата)
      </label>
      <label className="vs-check">
        <input type="checkbox" checked={limits} onChange={(e) => setLimits(e.target.checked)} />
        Контролировать пределы сверхурочной работы — при превышении предупреждать
        при вводе и согласовании
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
  const [from, setFrom] = useState(thisMonth());

  useEffect(() => {
    settingsApi.rules().then((page) => setRules(page.results))
      .catch((err) => setError(errorText(err)));
  }, []);

  function replace(row: ValidationRuleRow) {
    setRules((list) => (list ?? []).map((r) => (r.code === row.code ? row : r)));
  }

  return (
    <section className="vs-card vs-settings-section">
      <h2 className="vs-section-title">Правила проверки графика</h2>
      <p className="vs-muted vs-small">
        Пороги — данные, а не код. Новый порог действует с выбранного месяца:
        для правил о годе (минимум за год, непрерывный отпуск) — с 1 января
        года, к которому относится проверка, для остальных — с даты отпуска.
        Уже внесённые записи не пересчитываются, пока их не изменят
        или не отправят на согласование.
      </p>
      <MonthPicker label="Изменения порогов действуют с" value={from} onChange={setFrom} />
      {error && <div className="vs-error vs-error-inline">{error}</div>}
      {rules === null ? <div className="vs-muted vs-small">Загружаем…</div> : (
        <div className="vs-rules">
          {rules.map((rule) => (
            <RuleLine key={rule.code} rule={rule} from={from} onSaved={replace} />
          ))}
        </div>
      )}
    </section>
  );
}

function RuleLine({ rule, from, onSaved }: {
  rule: ValidationRuleRow;
  from: string;
  onSaved: (row: ValidationRuleRow) => void;
}) {
  const [value, setValue] = useState(rule.threshold_value === null ? "" : String(Number(rule.threshold_value)));
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  async function patch(data: Partial<ValidationRuleRow> & { effective_from?: string }) {
    setError(null);
    try {
      const row = await settingsApi.saveRule(rule.code, data);
      onSaved(row);
      setSaved(true);
      window.setTimeout(() => setSaved(false), 1500);
    } catch (err) {
      setError(errorText(err));
      setValue(rule.threshold_value === null ? "" : String(Number(rule.threshold_value)));
    }
  }

  function commit() {
    if (rule.threshold_value === null) return;
    if (Number(value) === Number(rule.threshold_value) && value !== "") return;
    void patch({ threshold_value: value === "" ? null : value, effective_from: from });
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
      <div className="vs-rule-error">
        <HistoryLines rows={rule.threshold_history} format={(v) => String(v ?? "")}
          onRemove={(month) => void settingsApi.removeHistory<ValidationRuleRow>(
            { key: "rule_threshold", effective_from: month, rule_code: rule.code })
            .then((row) => { onSaved(row); setValue(String(Number(row.threshold_value))); })
            .catch((err) => setError(errorText(err)))} />
      </div>
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
      if (!Object.keys(fieldErrors(err)).length) setStatus(errorText(err));
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

  useEffect(() => {
    settingsApi.rvdTracking().then(setData).catch((err) => setError(errorText(err)));
  }, []);

  async function set(kind: "unit" | "employee", id: string, value: boolean | null) {
    setError(null);
    try {
      setData(await settingsApi.setRvdTracking(kind, id, value));
    } catch (err) {
      setError(errorText(err));
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
        <EmployeePicker label={`Сотрудник — «${data.company ? "не учитывать" : "учитывать"}»`}
          onSelect={(e) => { if (e) void set("employee", e.id, !data.company); }} />
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

/** Своя норма отпуска — для категорий с дополнительным отпуском (ФЗ, п. 7.5). */
function VacationNormSection() {
  const [data, setData] = useState<VacationNormOverrides | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<EmployeeBrief | null>(null);
  const [days, setDays] = useState("");
  const [from, setFrom] = useState(thisMonth());

  useEffect(() => {
    settingsApi.vacationNorms().then(setData).catch((err) => setError(errorText(err)));
  }, []);

  async function set(id: string, value: number | null) {
    setError(null);
    try {
      setData(await settingsApi.setVacationNorm(id, value, from));
      return true;
    } catch (err) {
      setError(errorText(err));
      return false;
    }
  }

  async function remove(employeeId: string, month: string) {
    try {
      setData(await settingsApi.removeHistory<VacationNormOverrides>(
        { key: "employee_norm", effective_from: month, employee_id: employeeId }));
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function add() {
    if (!picked) return;
    if (await set(picked.id, Number(days))) {
      setPicked(null);
      setDays("");
    }
  }

  if (!data) return <section className="vs-card">{error ?? "Загружаем…"}</section>;
  const n = data.company_norm;
  const daysText = (v: number | null) => v === null ? "общая" : `${v} ${plural(v, "день", "дня", "дней")}`;

  return (
    <section className="vs-card vs-settings-section">
      <h2 className="vs-section-title">Своя норма отпуска</h2>
      <p className="vs-muted vs-small">
        Общая норма — {n} {plural(n, "день", "дня", "дней")} в год (выше). Сотруднику
        с дополнительным отпуском можно задать свою с выбранного месяца: от неё считаются
        минимум на год и потолок планирования, а начисление в месяц растёт в той же пропорции.
        Если норма меняется в середине года, годовая считается по месяцам.
      </p>
      {error && <div className="vs-error vs-error-inline">{error}</div>}

      {data.employees.length > 0 && (
        <div className="vs-table-scroll">
        <table className="vs-table">
          <tbody>
            {data.employees.map((e) => (
              <tr key={e.id}>
                <td>{e.name}{e.org_unit && <div className="vs-muted vs-small">{e.org_unit}</div>}</td>
                <td>
                  Сейчас: {daysText(e.norm)}
                  <div className="vs-muted vs-small">{String(e.monthly_rate).replace(".", ",")} в месяц</div>
                  <HistoryLines rows={e.history} format={daysText}
                    onRemove={(month) => void remove(e.id, month)} />
                </td>
                <td className="vs-override-cell">
                  {e.norm !== null && (
                    <button className="vs-link" onClick={() => void set(e.id, null)}>
                      Вернуть общую
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}

      <div className="vs-timeoff-form">
        <EmployeePicker label="Сотрудник" selected={picked} onSelect={(e) => setPicked(e)} />
        <label className="vs-field">
          Норма, дн.
          <input type="number" min={1} max={90} step={1} value={days}
            onChange={(e) => setDays(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") void add(); }} />
        </label>
        <MonthPicker label="Действует с" value={from} onChange={setFrom} />
        <button className="vs-btn" disabled={!picked || !days} onClick={() => void add()}>
          Задать норму
        </button>
      </div>
      <p className="vs-muted vs-small">
        «Вернуть общую» тоже действует с выбранного месяца.
      </p>
    </section>
  );
}

/** HR-администраторы: видят данные всех сотрудников, ничего не меняют. */
function HrViewersSection() {
  const [rows, setRows] = useState<HrViewer[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    settingsApi.hrViewers().then(setRows).catch((err) => setError(errorText(err)));
  }, []);

  async function set(id: string, value: boolean) {
    setError(null);
    try {
      setRows(await settingsApi.setHrViewer(id, value));
    } catch (err) {
      setError(errorText(err));
    }
  }

  if (!rows) return <section className="vs-card">{error ?? "Загружаем…"}</section>;

  return (
    <section className="vs-card vs-settings-section">
      <h2 className="vs-section-title">HR-администраторы</h2>
      <p className="vs-muted vs-small">
        Видят графики, отгулы и отсутствия всех сотрудников, календарь команды
        со всеми черновиками, теги и журнал изменений. Ничего не меняют
        и не согласуют — кроме своего графика, как любой сотрудник.
      </p>
      {error && <div className="vs-error vs-error-inline">{error}</div>}
      {rows.length > 0 && (
        <ul className="vs-list">
          {rows.map((r) => (
            <li className="vs-item" key={r.id}>
              <span>{r.full_name}</span>
              <span className="vs-muted vs-small">
                {r.org_unit ?? ""}{r.is_active ? "" : " · уволен"}
              </span>
              <button className="vs-x" onClick={() => void set(r.id, false)}
                aria-label={`Снять роль HR с ${r.full_name}`}>✕</button>
            </li>
          ))}
        </ul>
      )}
      <EmployeePicker label="Назначить сотрудника" exclude={new Set(rows.map((r) => r.id))}
        onSelect={(e) => { if (e) void set(e.id, true); }} />
    </section>
  );
}
