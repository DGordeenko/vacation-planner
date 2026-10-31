/** Формы ответов API. Соответствуют документу 06-API-DESIGN.md. */

export interface Me {
  id: string;
  full_name: string;
  email: string;
  employee_number: string;
  hire_date: string;
  org_unit: string | null;
  is_admin: boolean;
  is_manager: boolean;
  manager_name: string | null;
  tags: string[];
}

export type RecordType = "vacation" | "absence" | "rvd" | "overtime" | "day_off";
export type EntryStatus =
  | "draft" | "pending_approval" | "approved" | "rejected" | "cancelled";

export interface ScheduleEntry {
  id: string;
  employee: string;
  employee_name: string;
  record_type: RecordType;
  date_from: string;
  date_to: string | null;
  status: EntryStatus;
  vacation_days: number | null;
  extended_by_holiday_days: number;
  /** Нарушения черновика, которые ждут подтверждения руководителя. */
  pending_exceptions: Violation[];
  /** Причина, по которой руководитель не подтвердил отпуск. */
  rejection_comment: string;
}

/** Коллега, из-за отпуска которого исчерпан лимит по тегу. */
export interface ConflictingColleague {
  name: string;
  date_from: string;
  date_to: string;
}

export interface Violation {
  code: string;
  name: string;
  message: string;
  can_be_approved: boolean;
  colleagues: ConflictingColleague[];
}

export interface ValidationResult {
  is_valid: boolean;
  violations: Violation[];
  vacation_days: number;
  calendar_days: number;
  holiday_extension: { suggested_date_to: string; added_days: number };
}

export interface ColleagueOnLeave {
  employee_id: string;
  name: string;
  date_from: string;
  date_to: string;
}

export interface TagAvailability {
  tag_id: string;
  name: string;
  limit: number | null;
  source: string;
  colleagues_on_leave: ColleagueOnLeave[];
}

export interface ApprovalStepBrief {
  order: number;
  level_display: string;
  approver_name: string;
  status: "pending" | "approved" | "rejected" | "withdrawn";
  comment: string;
}

export interface AnnualSummary {
  year: number;
  annual_norm: number;
  planned_days: number;
  against_norm: number;
  from_accumulated: number;
  accumulated_stock: number;
  accumulated_stock_exact: number;
  ceiling: number;
  remaining: number;
  longest_continuous: number;
  min_continuous_required: number;
  absence_days: number;
  submitted: boolean;
  approval_id: string | null;
  approval_steps: ApprovalStepBrief[];
  year_approved: boolean;
  amendment_id: string | null;
  amendment_status: "open" | "pending" | null;
  manager_name: string | null;
  tags: TagAvailability[];
}

export interface Holiday {
  date: string;
  name: string;
  day_type: string;
}

export interface Branding {
  organization_name: string;
  brand_primary_color: string;
  brand_contrast_color: string;
  brand_radius: string;
  brand_font_family: string;
  brand_logo: string | null;
}

export interface Notification {
  id: string;
  notification_type: string;
  message: string;
  is_read: boolean;
  created_at: string;
}

export interface Paginated<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

/* --- Административный календарь --- */

export type DayType = "holiday" | "pre_holiday_short" | "transferred_workday";

export interface HolidayRow {
  id: string;
  date: string;
  name: string;
  day_type: DayType;
  day_type_display: string;
  weekday: number;
}

export interface YearSummary {
  year: number;
  count: number;
}

export interface ImportProblem {
  row: number;
  column: string;
  value: string;
  message: string;
}

export interface ImportReport {
  applied: boolean;
  total_rows: number;
  created: number;
  updated: number;
  problems: ImportProblem[];
  warnings: ImportProblem[];
  preview: { date: string; name: string; day_type: DayType }[];
}

export interface Invitation {
  employee_id: string;
  full_name: string;
  employee_number: string;
  email: string;
  org_unit: string | null;
  invitation_token: string;
}

export interface EmployeeImportReport {
  applied: boolean;
  dry_run: boolean;
  total_rows: number;
  created: number;
  updated: number;
  problems: ImportProblem[];
  warnings: ImportProblem[];
}

export interface AuditChange {
  field: string;
  label: string;
  was: unknown;
  became: unknown;
}

export interface AuditRow {
  id: string;
  entity_type: string;
  entity_type_display: string;
  entity_id: string | null;
  entity_label: string;
  action: "create" | "update" | "delete";
  action_display: string;
  changed_by: string | null;
  changed_by_name: string;
  subject_employee: string | null;
  subject_name: string | null;
  changed_at: string;
  diff: Record<string, unknown>;
  changes: AuditChange[];
  source: string;
}

export interface AuditEntityType {
  code: string;
  name: string;
}

export interface CompanySettingsRow {
  annual_vacation_norm_days: number;
  monthly_accrual_rate: number;
  approval_levels: number;
  workday_hours: number;
  rvd_compensation_tracking_enabled: boolean;
}

export interface ValidationRuleRow {
  code: string;
  name: string;
  description: string;
  is_enabled: boolean;
  /** Строка: DecimalField приходит из DRF строкой. null — у правила нет порога. */
  threshold_value: string | null;
  approver_level: string;
}

export interface CalendarImpact {
  year: number;
  affected_entries: number;
  affected_employees: number;
}

/* --- Способы входа --- */

export interface AuthProvider {
  name: "kerberos" | "keycloak";
  title?: string;
  configured: boolean;
  auto?: boolean;
}

export type DemoRoleCode = "employee" | "manager" | "admin";

export interface DemoRole {
  code: DemoRoleCode;
  title: string;
  description: string;
  full_name: string;
}

export interface DemoMethods {
  enabled: boolean;
  roles: DemoRole[];
}

export interface DemoLogin {
  token: string;
  employee_id: string;
  scheme: "Demo";
  role: DemoRoleCode;
}

export interface AuthMethods {
  password_login: boolean;
  providers: AuthProvider[];
  demo: DemoMethods;
}

/* --- Теги и лимиты --- */

export interface TagRow {
  id: string;
  name: string;
  max_simultaneous_absence: number | null;
  members_count: number;
  manual_count: number;
  is_org_unit_tag: boolean;
}

export interface TagMember {
  employee_id: string;
  full_name: string;
  employee_number: string;
  org_unit: string | null;
  source: "auto_org" | "manual";
  assigned_by: string | null;
  assigned_at: string;
}

export interface EmployeeBrief {
  id: string;
  full_name: string;
  employee_number: string;
  org_unit: string | null;
}

/* --- Решения руководителя и календарь команды --- */

export interface TeamEntry {
  id: string;
  record_type: RecordType;
  date_from: string;
  date_to: string;
  status: EntryStatus;
  vacation_days: number | null;
}

export interface ApprovalStepRow {
  id: string;
  order: number;
  level: string;
  level_display: string;
  approver: string;
  approver_name: string;
  status: string;
  comment: string;
  decided_at: string | null;
}

export interface ApprovalDecision {
  approval_id: string;
  step_id: string;
  employee_id: string;
  employee_name: string;
  year: number;
  scope: "initial" | "amendment";
  submitted_at: string;
  planned_days: number;
  entries: TeamEntry[];
  steps: ApprovalStepRow[];
}

export interface ExceptionDecision extends TeamEntry {
  employee_id: string;
  employee_name: string;
  violations: Violation[];
}

export interface TimeOffSummary {
  hours: number;
  workday_hours: number;
  available_days: number;
  remainder_hours: number;
}

export interface TimeOffBalance extends TimeOffSummary {
  /** Отгулы на согласовании: часы ещё не списаны, но заняты. */
  pending_days: number;
  /** Сколько отгулов можно попросить сейчас. */
  free_days: number;
  history: { date: string; hours: number; reason: string; comment: string }[];
}

export interface DayOffDecision extends TeamEntry {
  employee_id: string;
  employee_name: string;
  balance: TimeOffSummary;
}

export interface Decisions {
  count: number;
  approvals: ApprovalDecision[];
  exceptions: ExceptionDecision[];
  day_offs: DayOffDecision[];
}

export interface AbsenceReason {
  id: string;
  code: string;
  name: string;
  is_other: boolean;
}

export interface Absence {
  id: string;
  employee: string;
  employee_name: string;
  date_from: string;
  date_to: string | null;
  reason: string | null;
  reason_name: string | null;
  custom_reason_text: string;
  days: number;
  recorded_by: string;
  recorded_by_id: string | null;
  created_at: string;
}

export interface AbsenceSummary {
  year: number;
  total_days: number;
  episodes: number;
  by_reason: { reason: string; days: number; count: number }[];
}

export type RvdCompensation = "double_pay" | "pay_plus_day_off";

export interface RvdRow {
  id: string;
  employee: string;
  employee_name: string;
  employee_number: string;
  date_from: string;
  /** DecimalField приходит строкой */
  work_hours: string;
  rvd_compensation_type: RvdCompensation;
  compensation_display: string;
  task_link: string;
}

export interface CheckResult {
  is_valid: boolean;
  violations: Violation[];
}

export interface TeamCalendarEntry extends TeamEntry {
  /** У кого сейчас решение: согласующий графика или руководитель по исключению. */
  waiting_for: string | null;
}

export type TeamScope = "mine" | "all";

export interface TeamCalendar {
  date_from: string;
  date_to: string;
  scope: TeamScope;
  /** Руководитель может переключиться на график всех подразделений. */
  can_view_all: boolean;
  tag: { id: string; name: string; limit: number | null } | null;
  limit_reached: { date: string; count: number }[];
  rows: {
    employee_id: string;
    full_name: string;
    org_unit: string | null;
    /** Сотрудник из своей структуры; по чужим видны только планы без черновиков. */
    own: boolean;
    entries: TeamCalendarEntry[];
  }[];
}

export interface TeamChainStep extends ApprovalStepRow {
  is_current: boolean;
}

export interface TeamEntryDetail extends TeamEntry {
  employee_name: string;
  org_unit: string | null;
  steps: TeamChainStep[];
  exception: { approver_name: string; violations: Violation[] } | null;
}
