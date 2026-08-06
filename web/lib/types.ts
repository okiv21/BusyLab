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
