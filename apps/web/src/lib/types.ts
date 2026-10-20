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
export type Session = { user: User; org: Org; role: Role; csrf_token: string };
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
};

export type SousTurn = { role: "user" | "assistant"; content: string };

export type SousAskInput = { question: string; location_id: string; history?: SousTurn[] };
