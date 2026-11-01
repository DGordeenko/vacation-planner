import { useCallback, useEffect, useMemo, useState } from "react";
import { tagsApi } from "../api";
import type { EmployeeBrief, TagMember, TagRow } from "../types";
import EmployeePicker from "./EmployeePicker";

/** Справочник тегов: лимиты одновременного отсутствия и ручное назначение.
    Администратор меняет справочник и лимиты; руководитель назначает и снимает
    дополнительные теги сотрудникам своей структуры. Сотрудник сюда не попадает. */
export default function TagsAdmin({ onBack, canEdit }: { onBack: () => void; canEdit: boolean }) {
  const [tags, setTags] = useState<TagRow[]>([]);
  const [filter, setFilter] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [newLimit, setNewLimit] = useState("");

  const reload = useCallback(async () => {
    try {
      setTags(await tagsApi.list());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить теги");
    }
  }, []);

  useEffect(() => { void reload(); }, [reload]);

  const visible = useMemo(() => {
    const query = filter.trim().toLowerCase();
    return query ? tags.filter((t) => t.name.toLowerCase().includes(query)) : tags;
  }, [tags, filter]);

  async function create() {
    if (!newName.trim()) return;
    if (newLimit.trim() && parseLimit(newLimit) === null) {
      setError("Лимит — целое число больше нуля, или пусто для «без ограничения»");
      return;
    }
    try {
      await tagsApi.create(newName.trim(), parseLimit(newLimit));
      setNewName(""); setNewLimit("");
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось создать тег");
    }
  }

  return (
    <div className="vs-admin">
      <div className="vs-top">
        <button className="vs-link" onClick={onBack}>← К моему графику</button>
        <h1 className="vs-admin-title">{canEdit ? "Теги и лимиты" : "Теги сотрудников"}</h1>
      </div>

      <p className="vs-muted vs-small vs-team-note">
        {canEdit
          ? "Лимит — сколько сотрудников с тегом могут быть в отпуске одновременно. "
            + "Пусто — без ограничения. Теги подразделений создаются из кадровой "
            + "выгрузки; дополнительные назначаются вручную."
          : "Назначьте сотрудникам своей структуры дополнительные теги — по ним "
            + "проверяется лимит одновременного отсутствия. Лимиты задаёт "
            + "администратор; теги подразделений приходят из оргструктуры."}
      </p>

      {error && (
        <div className="vs-error vs-error-inline">
          {error}
          <button className="vs-link" onClick={() => setError(null)}>скрыть</button>
        </div>
      )}

      <div className="vs-team-controls">
        <label className="vs-field vs-inline-field">
          Найти тег
          <input value={filter} onChange={(e) => setFilter(e.target.value)} />
        </label>
        {canEdit && <div className="vs-new-tag">
          <label className="vs-field vs-inline-field">
            Новый тег
            <input value={newName} placeholder="Например, Доступ в ЦОД"
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void create()} />
          </label>
          <label className="vs-field vs-inline-field vs-limit-field">
            Лимит
            <input value={newLimit} inputMode="numeric" placeholder="—"
              onChange={(e) => setNewLimit(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void create()} />
          </label>
          <button className="vs-btn" onClick={create} disabled={!newName.trim()}>Создать</button>
        </div>}
      </div>

      <div className="vs-card vs-tags-table">
        <div className="vs-tags-head">
          <span>Тег</span><span>Тип</span><span>Сотрудников</span><span>Лимит</span>
        </div>
        {visible.map((tag) => (
          <TagLine key={tag.id} tag={tag} open={openId === tag.id} canEdit={canEdit}
            onToggle={() => setOpenId(openId === tag.id ? null : tag.id)}
            onChanged={reload} onError={setError} />
        ))}
        {visible.length === 0 && <div className="vs-muted vs-small">Ничего не найдено.</div>}
      </div>
    </div>
  );
}

function parseLimit(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const number = Number(trimmed);
  return Number.isInteger(number) && number > 0 ? number : null;
}

function TagLine({ tag, open, canEdit, onToggle, onChanged, onError }: {
  tag: TagRow;
  open: boolean;
  canEdit: boolean;
  onToggle: () => void;
  onChanged: () => void;
  onError: (message: string) => void;
}) {
  const [limit, setLimit] = useState(tag.max_simultaneous_absence?.toString() ?? "");
  const [saved, setSaved] = useState(false);

  useEffect(() => { setLimit(tag.max_simultaneous_absence?.toString() ?? ""); },
    [tag.max_simultaneous_absence]);

  async function saveLimit() {
    const value = parseLimit(limit);
    if (limit.trim() && value === null) {
      onError("Лимит — целое число больше нуля, или пусто для «без ограничения»");
      return;
    }
    if (value === tag.max_simultaneous_absence) return;
    try {
      await tagsApi.setLimit(tag.id, value);
      setSaved(true);
      window.setTimeout(() => setSaved(false), 1500);
      onChanged();
    } catch (err) {
      onError(err instanceof Error ? err.message : "Не удалось сохранить лимит");
    }
  }

  return (
    <div className={`vs-tag-line${open ? " open" : ""}`}>
      <div className="vs-tags-row">
        <button className="vs-link vs-tag-name" onClick={onToggle} aria-expanded={open}>
          {open ? "▾" : "▸"} {tag.name}
        </button>
        <span className="vs-muted vs-small">
          {tag.is_org_unit_tag ? "подразделение" : "дополнительный"}
        </span>
        <span>{tag.members_count}</span>
        <span className="vs-limit-cell">
          {!canEdit ? (
            <span>{tag.max_simultaneous_absence ?? "—"}</span>
          ) : <>
          <input value={limit} inputMode="numeric" placeholder="—" aria-label={`Лимит «${tag.name}»`}
            onChange={(e) => setLimit(e.target.value)} onBlur={saveLimit}
            onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} />
          {saved && <span className="vs-saved">сохранено</span>}
          </>}
        </span>
      </div>
      {open && <TagMembers tag={tag} canEdit={canEdit} onChanged={onChanged} onError={onError} />}
    </div>
  );
}

function TagMembers({ tag, canEdit, onChanged, onError }: {
  tag: TagRow;
  canEdit: boolean;
  onChanged: () => void;
  onError: (message: string) => void;
}) {
  const [members, setMembers] = useState<TagMember[] | null>(null);

  const load = useCallback(async () => {
    try {
      setMembers(await tagsApi.members(tag.id));
    } catch (err) {
      onError(err instanceof Error ? err.message : "Не удалось загрузить сотрудников");
    }
  }, [tag.id, onError]);

  useEffect(() => { void load(); }, [load]);

  async function assign(employee: EmployeeBrief) {
    try {
      await tagsApi.assign(tag.id, employee.id);
      await load(); onChanged();
    } catch (err) {
      onError(err instanceof Error ? err.message : "Не удалось назначить тег");
    }
  }

  async function unassign(member: TagMember) {
    try {
      await tagsApi.unassign(tag.id, member.employee_id);
      await load(); onChanged();
    } catch (err) {
      onError(err instanceof Error ? err.message : "Не удалось снять тег");
    }
  }

  const memberIds = new Set((members ?? []).map((m) => m.employee_id));

  return (
    <div className="vs-tag-members">
      {members === null ? <div className="vs-muted vs-small">Загружаем…</div> : (
        <ul className="vs-list">
          {members.map((member) => (
            <li className="vs-item" key={member.employee_id}>
              <span>{member.full_name}</span>
              <span className="vs-muted vs-small">
                {member.org_unit ?? ""}{member.source === "auto_org" ? " · по подразделению"
                  : member.assigned_by ? ` · назначил ${member.assigned_by}` : " · вручную"}
              </span>
              {member.source === "manual" && (
                <button className="vs-x" onClick={() => unassign(member)}
                  aria-label={`Снять тег с ${member.full_name}`}>✕</button>
              )}
            </li>
          ))}
          {members.length === 0 && <li className="vs-muted vs-small">Пока никого.</li>}
        </ul>
      )}
      {!canEdit && (
        <p className="vs-muted vs-small">Показаны сотрудники вашей структуры.</p>
      )}
      <EmployeePicker label="Добавить сотрудника" exclude={memberIds}
        onSelect={(e) => { if (e) void assign(e); }} />
    </div>
  );
}
