// API types. Source of truth: docs/api.md. Money is integer cents; percentages
// are in percent units (28.4 = 28.4%); null means "not computable".

export type User = { id: string; email: string; display_name: string };
export type Org = { id: string; name: string; timezone: string };
export type Role = "owner" | "admin" | "manager" | "employee";
export type Location = {
  id: string;
  name: string;
  address: string;
  timezone: string;
  business_day_start: string; // "04:00"
  archived_at: string | null;
};

export type FreshnessStatus = "fresh" | "stale" | "failing" | "not_connected";
export type Freshness = {
  stream: "orders" | "timecards" | "catalog" | "team_members";
  location_id: string;
  data_as_of: string | null;
  status: FreshnessStatus;
};

export type StreamName = "locations" | "catalog" | "orders" | "team_members" | "timecards";
export type StreamHealth = {
  stream: StreamName;
  status: "fresh" | "stale" | "failing" | "pending";
  data_as_of: string | null;
  last_success_at: string | null;
  last_error: string | null;
  backfill_target_from: string | null; // date
  /** Backfill runs newest -> oldest, so this is the OLDEST date done. */
  backfill_completed_through: string | null; // date
};

export type LocationLink = {
  external_location_id: string;
  external_name: string;
  location_id: string | null;
};

export type ConnectionStatus = "pending" | "active" | "needs_reauth" | "error" | "disconnected";
export type Connection = {
  id: string;
  provider: "square";
  mode: "fake" | "sandbox";
  status: ConnectionStatus;
  external_account_id: string | null;
  created_at: string;
  location_links: LocationLink[];
  streams: StreamHealth[];
};
export type ConnectionDetail = Connection & { recent_runs: SyncRun[] };

export type SyncRun = {
  id: string;
  stream: string;
  kind: "incremental" | "backfill" | "webhook";
  status: "running" | "succeeded" | "failed";
  started_at: string;
  finished_at: string | null;
  fetched_count: number;
  changed_count: number;
  error_detail: string | null;
};

export type MappingIssueKind =
  | "unmapped_location"
  | "unmapped_item"
  | "unmapped_team_member"
  | "unknown_channel";
export type MappingIssue = {
  id: string;
  kind: MappingIssueKind;
  external_id: string;
  label: string;
  occurrences: number;
  first_seen_at: string;
  last_seen_at: string;
  resolved_at: string | null;
  resolution: "ignored" | "mapped" | null;
  resolved_entity_id: string | null;
  new_since_resolved: number;
  mappable: boolean;
};
export type MappingCandidate = { id: string; label: string };

export type Channel = "dine_in" | "pickup" | "delivery" | "online" | "other";

export type Totals = {
  net_sales: number;
  gross_sales: number;
  discounts: number;
  returns: number;
  tax: number;
  tips: number;
  orders: number;
  avg_check: number | null;
  labor_cost: number;
  labor_hours: number;
  labor_pct: number | null;
};

// ---- Auth ----
export type Session = { user: User; org: Org; role: Role; csrf_token: string; has_employee: boolean };
export type SignupResponse = Session & { location: Location };
export type SignupBody = {
  email: string;
  password: string;
  display_name: string;
  org_name: string;
  location_name: string;
  timezone: string;
};

// ---- Lists ----
export type List<T> = { data: T[] };

// ---- Locations ----
export type LocationCreate = {
  name: string;
  address?: string;
  timezone: string;
  business_day_start?: string;
};
export type LocationPatch = Partial<Omit<Location, "id" | "archived_at">>;

// ---- Commerce ----
export type DailyRow = Totals & { business_date: string; channels: Record<Channel, number> };
export type DailyResponse = {
  location_id: string;
  currency: "USD";
  days: DailyRow[];
  freshness: Freshness[];
};

export type CompareMode = "previous_period" | "previous_year";
export type DateRange = { from: string; to: string };
export type SummaryResponse = {
  currency: "USD";
  period: DateRange;
  compare_period: DateRange;
  current: Totals;
  previous: Totals;
  change: {
    net_sales_pct: number | null;
    orders_pct: number | null;
    avg_check_pct: number | null;
    labor_pct_pts: number | null;
  };
  channels: Array<{ channel: Channel; net_sales: number; share_pct: number }>;
  freshness: Freshness[];
};

export type ItemRow = {
  name: string;
  category: string;
  quantity: number;
  net_sales: number;
  share_pct: number;
};
export type ItemsResponse = {
  currency: "USD";
  items: ItemRow[];
  unmapped_net_sales: number;
};

export type TodayResponse = {
  currency: "USD";
  business_date: string;
  net_sales: number;
  orders: number;
  avg_check: number | null;
  last_order_at: string | null;
  by_hour: Array<{ hour: number; net_sales: number; orders: number }>;
  freshness: Freshness[];
};

export type SousStatus = {
  provider: "ollama" | "fake";
  model: string;
  available: boolean;
  prompt_version: string;
  /** Absent on older APIs; treat as "closed". */
  breaker?: "closed" | "open" | "half_open";
};

export type SousKind = "answer" | "clarify" | "refuse" | "unsupported" | "evidence_only";
export type SousUnit = "usd_cents" | "count" | "percent" | "percent_pts" | "hours";

export type SousEvidence = {
  label: string;
  value: number;
  unit: SousUnit;
  source: string;
  as_of: string | null;
};

export type SousToolCall = { name: string; args: Record<string, unknown>; ms: number; ok: boolean };

export type SousAnswer = {
  kind: SousKind;
  answer: string;
  period: { from: string; to: string; timezone: string } | null;
  evidence: SousEvidence[];
  limitations: string[];
  tools_used: SousToolCall[];
  model: string;
  prompt_version: string;
  request_id: string;
  mode: "model" | "fallback";
};

export type SousStatusEvent = { label: string };
export type SousToolEvent = { name: string; args: Record<string, unknown>; ms: number; ok: boolean; label: string };
export type SousErrorEvent = { status: number; code: string; detail: string };

export type SousTurn = { role: "user" | "assistant"; content: string };

export type SousAskInput = { question: string; location_id: string; history?: SousTurn[] };

// ---- Team ----
export type Position = { id: string; name: string; color: string; archived_at: string | null };
export type Station = { id: string; location_id: string; name: string; archived_at: string | null };
export type EmployeePosition = { id: string; name: string; color: string; is_primary: boolean };
export type LoginStatus = "none" | "invited" | "active";
export type Employee = {
  id: string;
  display_name: string;
  email: string | null;
  phone: string | null;
  source: "square" | "manual";
  active: boolean;
  job_title: string | null;
  /** Synced from the POS (read-only for Square employees). */
  pos_hourly_rate: number | null;
  rate_override: number | null;
  /** rate_override ?? pos_hourly_rate; null = "no rate". */
  effective_rate: number | null;
  currency: "USD";
  positions: EmployeePosition[];
  location_ids: string[];
  login: LoginStatus;
  merged_into_id: string | null;
  updated_at: string;
};
export type PositionRef = { position_id: string; is_primary?: boolean };
export type EmployeeCreate = {
  display_name: string;
  email?: string | null;
  phone?: string | null;
  location_ids: string[];
  positions?: PositionRef[];
  rate_override?: number | null;
};
export type EmployeePatch = Partial<Omit<EmployeeCreate, "positions">> & {
  positions?: PositionRef[];
  active?: boolean;
};

export type InvitationStatus = "pending" | "accepted" | "revoked" | "expired";
export type Invitation = {
  id: string;
  email: string;
  role: "manager" | "employee";
  employee_id: string | null;
  employee_name: string | null;
  location_ids: string[];
  status: InvitationStatus;
  created_at: string;
  expires_at: string;
  accepted_at: string | null;
};
/** `accept_url` is shown once: only its hash is stored. */
export type InvitationCreated = { invitation: Invitation; accept_url: string };
export type InvitationPreview = {
  org_name: string;
  email: string;
  role: "manager" | "employee";
  employee_name: string | null;
  expires_at: string;
};

// ---- Schedule ----
export type ConflictKind =
  | "overlap"
  | "time_off_approved"
  | "time_off_pending"
  | "unavailable"
  | "inactive_employee"
  | "not_assigned_to_location"
  | "unassigned";
export type Shift = {
  id: string;
  location_id: string;
  employee_id: string | null;
  position_id: string;
  station_id: string | null;
  business_date: string;
  starts_at: string;
  ends_at: string;
  break_minutes: number;
  notes: string;
  paid_hours: number;
  /** Cents; null when there is no rate (or no one assigned). */
  cost: number | null;
  updated_at: string;
};
export type ShiftIn = {
  employee_id: string | null;
  position_id: string;
  station_id: string | null;
  starts_at: string;
  ends_at: string;
  break_minutes: number;
  notes: string;
};
export type Conflict = { shift_id: string; kind: ConflictKind; detail: string };
export type ScheduleEmployee = {
  id: string;
  display_name: string;
  active: boolean;
  primary_position_id: string | null;
  position_ids: string[];
  has_rate: boolean;
  assigned: boolean;
};
export type ScheduleTotals = {
  scheduled_hours: number;
  projected_cost: number;
  unrated_shifts: number;
  forecast_sales: number | null;
  projected_labor_pct: number | null;
};
export type ScheduleDay = ScheduleTotals & {
  business_date: string;
  forecast_basis: string[];
  forecast_reason: "not_connected" | "not_enough_history" | null;
};
export type ScheduleResponse = {
  location_id: string;
  timezone: string;
  currency: "USD";
  schedule: {
    id: string | null;
    week_start: string;
    version: number;
    published_version: number | null;
    published_at: string | null;
    has_unpublished_changes: boolean;
    unpublished_changes: number;
  };
  shifts: Shift[];
  employees: ScheduleEmployee[];
  days: ScheduleDay[];
  week: ScheduleTotals & { reference_labor_pct: number | null; reference_period: DateRange };
  conflicts: Conflict[];
  freshness: Freshness[];
};

export type PublishResult = {
  published_version: number | null;
  /** False for a republish with no changes (nothing happened). */
  changed: boolean;
  changed_employees: string[];
  notified_users: number;
  employees_without_login: Array<{ id: string; display_name: string }>;
};

// ---- Notifications ----
export type NotificationKind =
  | "schedule_published"
  | "schedule_changed"
  | "time_off_decided"
  | "availability_decided"
  | "time_off_requested"
  | "availability_submitted"
  | "employee_deactivated"
  | "shift_late"
  | "shift_missed";
export type AppNotification = {
  id: string;
  kind: NotificationKind | string;
  title: string;
  body: string;
  link: string | null;
  entity_type: string | null;
  entity_id: string | null;
  created_at: string;
  read_at: string | null;
};
export type NotificationList = { data: AppNotification[]; unread_count: number };

// ---- Employee self-service (/v1/me) ----
export type MyShift = {
  id: string;
  location_id: string;
  business_date: string;
  starts_at: string;
  ends_at: string;
  break_minutes: number;
  position_name: string;
  position_color: string;
  station_name: string | null;
  notes: string;
};
export type TeamShift = {
  location_id: string;
  business_date: string;
  starts_at: string;
  ends_at: string;
  employee_name: string;
  position_name: string;
  position_color: string;
};
export type MyWeek = {
  week_start: string;
  employee: { id: string; display_name: string };
  published_at: string | null;
  locations: Array<{ id: string; name: string; timezone: string }>;
  my_shifts: MyShift[];
  team_shifts: TeamShift[];
};

// ---- Availability and time off ----
/** An unavailable window on a weekday (0 = Monday), location-local. No times = all day. */
export type AvailabilityBlock = { weekday: number; start_time: string | null; end_time: string | null };
export type AvailabilitySet = {
  id: string;
  employee_id: string;
  employee_name: string | null;
  status: "pending" | "approved" | "rejected" | "superseded";
  effective_from: string;
  note: string;
  blocks: AvailabilityBlock[];
  submitted_at: string;
  decided_at: string | null;
  decision_note: string | null;
};
export type MyAvailability = {
  current: AvailabilitySet | null;
  pending: AvailabilitySet | null;
  upcoming: AvailabilitySet[];
};
export type TimeOffStatus = "pending" | "approved" | "denied" | "cancelled";
export type TimeOff = {
  id: string;
  employee_id: string;
  employee_name: string | null;
  starts_on: string;
  ends_on: string;
  start_time: string | null;
  end_time: string | null;
  reason: string;
  status: TimeOffStatus;
  created_at: string;
  decided_at: string | null;
  decision_note: string | null;
  overlapping_shifts: Array<{
    id: string;
    location_id: string;
    business_date: string;
    starts_at: string;
    ends_at: string;
    position_name: string;
  }>;
};
export type TimeOffIn = {
  starts_on: string;
  ends_on: string;
  start_time?: string | null;
  end_time?: string | null;
  reason?: string;
};
export type RequestsInbox = { time_off: TimeOff[]; availability: AvailabilitySet[] };

// ---- Attendance (/v1/team/attendance) ----
export type AttendanceStatus = "upcoming" | "awaiting_data" | "on_time" | "late" | "missed";
export type AttendanceShift = {
  shift_id: string;
  employee_id: string | null;
  employee_name: string | null;
  position_id: string;
  position_name: string;
  position_color: string;
  starts_at: string;
  ends_at: string;
  scheduled_hours: number;
  clock_in: string | null;
  clock_out: string | null;
  actual_hours: number;
  status: AttendanceStatus;
  minutes_late: number | null;
};
export type UnscheduledTimecard = {
  timecard_id: string;
  employee_id: string | null;
  employee_name: string | null;
  clock_in: string;
  clock_out: string | null;
  actual_hours: number;
};
export type AttendanceDay = {
  business_date: string;
  scheduled_hours: number;
  actual_hours: number;
  counts: Record<AttendanceStatus, number>;
  shifts: AttendanceShift[];
  unscheduled: UnscheduledTimecard[];
};
export type AttendanceResponse = {
  location_id: string;
  timezone: string;
  period: { from: string; to: string };
  grace_minutes: number;
  as_of: string | null;
  open_shifts_excluded: number;
  days: AttendanceDay[];
  freshness: Freshness[];
};
