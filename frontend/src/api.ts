/** Клиент REST API.

Вся бизнес-логика — на сервере. Здесь только транспорт: интерфейс
не считает отпускные дни и не проверяет правила самостоятельно,
иначе две реализации со временем разойдутся.
*/

import type {
  AnnualSummary, AuthMethods, Branding, CalendarImpact, DayType, Decisions, DemoLogin,
  DemoRoleCode, EmployeeBrief, EmployeeImportReport, Holiday, HolidayRow, TagMember, TagRow, TeamCalendar,
  TeamEntryDetail, TeamScope,
  ImportReport, Me, Notification, Paginated, ScheduleEntry,
  ValidationResult, YearSummary,
} from "./types";

/* Пустой VITE_API_BASE_URL — относительный путь: nginx проксирует /api
   на бэкенд (Docker, Kubernetes). Заданный — абсолютный адрес бэкенда
   для площадок, где сервисы живут на разных доменах (Railway). */
const API_ROOT = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/+$/, "");
const BASE = `${API_ROOT}/api/v1`;
const TOKEN_KEY = "vs_token";
const SCHEME_KEY = "vs_token_scheme";

/* Схема заголовка авторизации. Demo — вход в демо-режим: сервер выполняет
   такой запрос целиком на отдельной демо-базе. */
export type TokenScheme = "Token" | "Demo";

let token: string | null = null;
let scheme: TokenScheme = "Token";

export function setToken(value: string | null) {
  token = value;
}

export function getToken() {
  return token;
}

export class ApiError extends Error {
  status: number;
  payload: unknown;
  constructor(status: number, message: string, payload: unknown) {
    super(message);
    this.status = status;
    this.payload = payload;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `${scheme} ${token}`;

  const response = await fetch(BASE + path, {
    ...init,
    headers: { ...headers, ...(init.headers as Record<string, string>) },
  });

  if (response.status === 204) return undefined as T;

  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message =
      (body as { detail?: string }).detail || `Ошибка запроса (${response.status})`;
    throw new ApiError(response.status, message, body);
  }
  return body as T;
}

export const api = {
  login: (email: string, password: string) =>
    request<{ token: string; employee_id: string }>("/auth/login/", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),

  acceptInvitation: (invitation_token: string, password: string) =>
    request<{ token: string; employee_id: string }>("/auth/accept-invitation/", {
      method: "POST",
      body: JSON.stringify({ invitation_token, password }),
    }),

  /** Демо-режим: вход выбором роли, без пароля. */
  demoLogin: (role: DemoRoleCode) =>
    request<DemoLogin>("/auth/demo-login/", {
      method: "POST",
      body: JSON.stringify({ role }),
    }),

  me: () => request<Me>("/auth/me/"),

  authMethods: () => request<AuthMethods>("/auth/methods/"),

  /** Kerberos: браузер сам приложит билет, если состоит в домене. */
  negotiate: () =>
    request<{ token: string; employee_id: string }>("/auth/negotiate/"),

  oidcStart: () =>
    request<{ authorize_url: string; state: string }>("/auth/oidc/start/"),

  oidcCallback: (code: string, state: string) =>
    request<{ token: string; employee_id: string }>(
      `/auth/oidc/callback/?code=${encodeURIComponent(code)}` +
      `&state=${encodeURIComponent(state)}`),

  branding: () => request<Branding>("/settings/branding/"),

  holidays: (year: number) => request<Holiday[]>(`/holidays/?year=${year}`),

  summary: (employeeId: string, year: number) =>
    request<AnnualSummary>(`/employees/${employeeId}/annual-summary/?year=${year}`),

  entries: (employeeId: string, year: number) =>
    request<Paginated<ScheduleEntry>>(
      `/schedule-entries/?employee_id=${employeeId}` +
      `&date_from=${year}-01-01&date_to=${year}-12-31&page_size=200`
    ),

  /** Предварительная проверка — та же логика, что при сохранении. */
  validate: (date_from: string, date_to: string) =>
    request<ValidationResult>("/schedule-entries/validate/", {
      method: "POST",
      body: JSON.stringify({ date_from, date_to, record_type: "vacation" }),
    }),

  createEntry: (date_from: string, date_to: string) =>
    request<ScheduleEntry>("/schedule-entries/", {
      method: "POST",
      body: JSON.stringify({ date_from, date_to, record_type: "vacation" }),
    }),

  deleteEntry: (id: string) =>
    request<void>(`/schedule-entries/${id}/`, { method: "DELETE" }),

  submitYear: (employeeId: string, year: number) =>
    request<{ id: string; status: string }>(
      `/employees/${employeeId}/submit-year/?year=${year}`, { method: "POST" }
    ),

  openAmendment: (employeeId: string, year: number) =>
    request<{ id: string; status: string }>(
      `/employees/${employeeId}/amendments/?year=${year}`, { method: "POST" }),

  submitAmendment: (amendmentId: string) =>
    request<{ id: string; status: string }>(
      `/amendments/${amendmentId}/submit/`, { method: "POST" }),

  cancelAmendment: (amendmentId: string) =>
    request<{ id: string; status: string }>(
      `/amendments/${amendmentId}/cancel/`, { method: "POST" }),

  withdraw: (approvalId: string) =>
    request<{ status: string }>(`/schedule-approvals/${approvalId}/withdraw/`, {
      method: "POST",
    }),

  notifications: () =>
    request<Paginated<Notification>>("/notifications/?page_size=20"),

  markRead: (id: string) =>
    request<void>(`/notifications/${id}/mark-read/`, { method: "POST" }),
};

/** Экраны руководителя: решения и календарь команды. */
export const managerApi = {
  decisions: () => request<Decisions>("/decisions/"),

  approveStep: (approvalId: string, stepId: string, comment: string) =>
    request<unknown>(`/schedule-approvals/${approvalId}/steps/${stepId}/approve/`, {
      method: "POST", body: JSON.stringify({ comment }),
    }),

  rejectStep: (approvalId: string, stepId: string, comment: string) =>
    request<unknown>(`/schedule-approvals/${approvalId}/steps/${stepId}/reject/`, {
      method: "POST", body: JSON.stringify({ comment }),
    }),

  /** Подтверждение исключения — по одному правилу за вызов. */
  approveException: (entryId: string, rule_code: string, comment: string) =>
    request<unknown>(`/schedule-entries/${entryId}/exception-approvals/`, {
      method: "POST", body: JSON.stringify({ rule_code, comment }),
    }),

  rejectException: (entryId: string, comment: string) =>
    request<unknown>(`/schedule-entries/${entryId}/exception-rejection/`, {
      method: "POST", body: JSON.stringify({ comment }),
    }),

  teamCalendar: (date_from: string, date_to: string, tagId: string | null,
    scope: TeamScope = "mine") =>
    request<TeamCalendar>(`/team-calendar/?date_from=${date_from}&date_to=${date_to}`
      + (tagId ? `&tag_id=${tagId}` : "") + (scope === "all" ? "&scope=all" : "")),
  teamEntry: (id: string) => request<TeamEntryDetail>(`/team-calendar/entries/${id}/`),
};

/** Справочник тегов и ручное назначение. */
export const tagsApi = {
  list: () => request<TagRow[]>("/tags/"),

  create: (name: string, max_simultaneous_absence: number | null) =>
    request<TagRow>("/tags/", {
      method: "POST", body: JSON.stringify({ name, max_simultaneous_absence }),
    }),

  setLimit: (id: string, max_simultaneous_absence: number | null) =>
    request<TagRow>(`/tags/${id}/`, {
      method: "PATCH", body: JSON.stringify({ max_simultaneous_absence }),
    }),

  remove: (id: string) => request<void>(`/tags/${id}/`, { method: "DELETE" }),

  members: (id: string) => request<TagMember[]>(`/tags/${id}/members/`),

  assign: (id: string, employee_id: string) =>
    request<TagMember>(`/tags/${id}/assign/`, {
      method: "POST", body: JSON.stringify({ employee_id }),
    }),

  unassign: (id: string, employeeId: string) =>
    request<void>(`/tags/${id}/unassign/${employeeId}/`, { method: "DELETE" }),

  searchEmployees: (q: string) =>
    request<EmployeeBrief[]>(`/employees/search/?q=${encodeURIComponent(q)}`),
};

/** Администрирование календаря праздников. */
/** Загрузка кадровой выгрузки. Файл уходит байтами (base64): сервер сам
    определяет кодировку, UTF-8 или CP1251, как у выгрузок из 1С. */
export const employeesAdminApi = {
  importCsv: (contentBase64: string, dryRun: boolean) =>
    request<EmployeeImportReport>("/employees/import-csv/", {
      method: "POST",
      body: JSON.stringify({ content: contentBase64, dry_run: dryRun }),
    }),
};

export const adminApi = {
  years: () => request<YearSummary[]>("/admin/holidays/years/"),

  list: (year: number) =>
    request<Paginated<HolidayRow>>(`/admin/holidays/?year=${year}&page_size=400`),

  create: (date: string, name: string, day_type: DayType) =>
    request<HolidayRow>("/admin/holidays/", {
      method: "POST",
      body: JSON.stringify({ date, name, day_type }),
    }),

  update: (id: string, patch: Partial<Pick<HolidayRow, "name" | "day_type">>) =>
    request<HolidayRow>(`/admin/holidays/${id}/`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),

  remove: (id: string) =>
    request<void>(`/admin/holidays/${id}/`, { method: "DELETE" }),

  clearYear: (year: number) =>
    request<{ deleted: number }>(`/admin/holidays/clear-year/?year=${year}`, {
      method: "DELETE",
    }),

  copyYear: (source_year: number, target_year: number) =>
    request<{ created: number; skipped: string[]; target_year: number }>(
      "/admin/holidays/copy-year/", {
        method: "POST",
        body: JSON.stringify({ source_year, target_year }),
      }),

  /** dry_run=true возвращает разбор файла без записи — для предпросмотра. */
  importCsv: (content: string, year: number, replace: boolean, dry_run: boolean) =>
    request<ImportReport>("/admin/holidays/import/", {
      method: "POST",
      body: JSON.stringify({ content, year, replace, dry_run }),
    }),

  impact: (year: number) =>
    request<CalendarImpact>(`/admin/holidays/impact/?year=${year}`),
};

/** Токен живёт в памяти вкладки; в браузере — в sessionStorage. */
export function restoreToken() {
  try {
    const stored = sessionStorage.getItem(TOKEN_KEY);
    if (stored) {
      token = stored;
      scheme = sessionStorage.getItem(SCHEME_KEY) === "Demo" ? "Demo" : "Token";
    }
    return stored;
  } catch {
    return null;
  }
}

export function persistToken(value: string | null, tokenScheme: TokenScheme = "Token") {
  token = value;
  scheme = value ? tokenScheme : "Token";
  try {
    if (value) {
      sessionStorage.setItem(TOKEN_KEY, value);
      sessionStorage.setItem(SCHEME_KEY, scheme);
    } else {
      sessionStorage.removeItem(TOKEN_KEY);
      sessionStorage.removeItem(SCHEME_KEY);
    }
  } catch {
    /* приватный режим браузера — токен останется только в памяти */
  }
}

/** Открыт ли сейчас демо-режим: интерфейс показывает об этом плашку. */
export function isDemo() {
  return Boolean(token) && scheme === "Demo";
}
