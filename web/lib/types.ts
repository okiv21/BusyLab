/** Mirrors what the API serves. Kept hand-written and small: the frontend
 *  should read findings, never reconstruct the analysis behind them. */

export type Severity = "urgent" | "watch" | "neutral" | "good";

export type ChartType =
  | "line_with_band"
  | "bar_horizontal"
  | "donut"
  | "treemap"
  | "scatter"
  | "diverging_bars"
  | "waterfall"
  | "grouped_bars"
  | "small_multiples"
  | "stacked_area"
  | "forecast_fan"
  | "quadrant"
  | "cohort_heatmap"
  | "correlation_heatmap"
  | "progress_arc"
  | "callout";

export interface Evidence {
  method: string;
  p_value: number | null;
  adjusted_p: number | null;
  sample_size: number | null;
  correction: string | null;
  strength: string;
  notes: string[];
}

export interface Finding {
  id: string;
  type: string;
  chart: ChartType | null;
  summary: string;
  facts: Record<string, any>;
  severity: Severity;
  importance: number;
  tier: string;
  evidence: Evidence;
  chart_data: Record<string, any>;
  related: string[];
  narrated_by?: "model" | "engine";
  /** What the summary means, in plain words and without numbers. */
  meaning?: string;
  /** Definitions for the jargon this particular summary could not avoid. */
  glossary?: Record<string, string>;
}

export interface Chip {
  name: string;
  label: string;
}

export interface QualityIssue {
  code: string;
  severity: "block" | "warn" | "info";
  title: string;
  detail: string;
  count: number | null;
  sample: string[];
}

export interface Quality {
  passed: boolean;
  headline: string;
  issues: QualityIssue[];
}

export interface Story {
  findings: Finding[];
  tiers: Record<string, boolean>;
  locked: { tier: string; prompt: string }[];
  notes: string[];
  errors: string[];
  chips: Chip[];
  columns: string[];
  held: boolean;
  quality: Quality | null;
}

export interface RoleOption {
  role: string;
  label: string;
}

export interface Prompt {
  column: string;
  question: string;
  reason: string;
  suggested: string | null;
  suggested_label: string;
  options: RoleOption[];
  allow_ignore: boolean;
  allow_group_by: boolean;
}

export interface ConfirmedColumn {
  role: string;
  label: string;
  column: string;
  confidence: number;
  reason: string;
}

export interface TierState {
  tier: string;
  label: string;
  unlocked: boolean;
  locked_prompt: string;
}

export interface Columns {
  rows: number;
  fingerprint: string;
  ready: boolean;
  confirmed: ConfirmedColumn[];
  prompts: Prompt[];
  unknown_columns: string[];
  missing: { role: string; label: string }[];
  tiers: TierState[];
  notes: string[];
  cost_basis: string | null;
  reused_mapping: boolean;
  loader: {
    summary: string;
    sheets_used: string[];
    dropped_total_rows: number;
    notes: string[];
  };
}

export interface Job {
  id: string;
  kind: string;
  dataset_id: string;
  status: "pending" | "running" | "done" | "failed";
  step: string;
  error: string | null;
  result?: unknown;
}

export interface Answer {
  answered: boolean;
  message?: string;
  route?: { name: string; label: string };
  confidence?: number;
  routed_by?: string;
  finding?: Finding;
  answer?: string;
  suggestions?: Chip[];
  /** Findings the answer rests on, so it can be traced to a computation. */
  sources?: string[];
  /** "model" when generated and verified, "engine" when it fell back. */
  answered_by?: "model" | "engine";
  /** Suggestions. Never render without advice_caution. */
  advice?: string;
  /** The caution that must accompany advice. */
  advice_caution?: string;
  /** Why a generated answer was discarded, when one was. */
  rejected?: string[];
}

export interface Goal {
  id: string;
  metric: "revenue" | "profit";
  target: number;
  start: string;
  end: string;
  label: string;
}

export type NewGoal = Omit<Goal, "id">;

export interface Alert {
  key: string;
  kind: string;
  level: "high" | "medium" | "good" | "info";
  title: string;
  detail: string;
  subject: string;
  period: string;
  finding_id: string | null;
  acknowledged?: boolean;
}

export interface DigestPreview {
  subject: string;
  headline: string;
  lines: string[];
  good_news: string | null;
  is_empty: boolean;
  html: string;
  text: string;
  /** Who it goes to and when. Absent on older builds. */
  delivery?: DigestDelivery;
}


/** Where a target actually stands, computed by the engine. */
export interface GoalProgress {
  goal_id: string;
  says: string;
  meaning?: string;
  severity: Severity;
  facts: Record<string, any>;
}

/** Who the digest goes to and when, so the preview is not just a picture. */
export interface DigestDelivery {
  recipient: string;
  is_fallback: boolean;
  /** False means it is written to the server log rather than emailed. */
  can_send: boolean;
  mailer: string;
  schedule: string;
}
