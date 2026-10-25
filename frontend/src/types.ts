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

export type RecordType = "vacation" | "absence" | "rvd" | "overtime";
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
