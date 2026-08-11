"use client";

/**
 * Setting a target (spec Pillar 4).
 *
 * The one place in the app where the user supplies something rather than
 * reading a result, so it stays small: a metric, an amount, and a window.
 * Everything else about the goal - pace, projection, where the gap lives - is
 * computed by the engine and comes back as a finding like any other.
 */

import { useState } from "react";
import { motion } from "framer-motion";
import { createGoal, deleteGoal } from "@/lib/api";
import type { Goal, GoalProgress } from "@/lib/types";

/** Sensible default window: the rest of the current quarter. */
function defaultWindow(): { start: string; end: string } {
  const now = new Date();
  const quarter = Math.floor(now.getMonth() / 3);
  const start = new Date(now.getFullYear(), quarter * 3, 1);
  const end = new Date(now.getFullYear(), quarter * 3 + 3, 0);
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  return { start: iso(start), end: iso(end) };
}

export default function GoalPanel({
  datasetId,
  goals,
  progress = [],
  hasProfit,
  onChanged,
}: {
  datasetId: string;
  goals: Goal[];
  progress?: GoalProgress[];
  hasProfit: boolean;
  onChanged: () => void;
}) {
  const progressFor = (id: string) => progress.find((p) => p.goal_id === id);
  const [open, setOpen] = useState(false);
  const [metric, setMetric] = useState<"revenue" | "profit">("revenue");
  const [target, setTarget] = useState("");
  const [label, setLabel] = useState("");
  const [window, setWindow] = useState(defaultWindow);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const amount = Number(target.replace(/[^0-9.]/g, ""));
    if (!Number.isFinite(amount) || amount <= 0) {
      setError("Enter a target amount.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await createGoal(datasetId, {
        metric,
        target: amount,
        start: window.start,
        end: window.end,
        label: label.trim(),
      });
      setTarget("");
      setLabel("");
      setOpen(false);
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "That did not work.");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: string) => {
    setBusy(true);
    try {
      await deleteGoal(datasetId, id);
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="card"
      style={{
        padding: "20px 24px",
        display: "flex",
        flexDirection: "column",
        gap: 14,
        background: "var(--card)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 12,
          flexWrap: "wrap",
        }}
      >
        <div>
          <div style={{ font: "600 15px var(--font-display)" }}>Your targets</div>
          <div style={{ fontSize: 13.5, color: "var(--ink-muted)", marginTop: 3 }}>
            Set one and BusyLab tracks pace against it in the story above.
          </div>
        </div>
        <button
          className={open ? "btn btn-ghost" : "btn"}
          onClick={() => setOpen((v) => !v)}
          disabled={busy}
          style={{ fontSize: 13.5, padding: "10px 18px" }}
        >
          {open ? "Cancel" : "Set a target"}
        </button>
      </div>

      {goals.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {goals.map((goal) => (
            <div
              key={goal.id}
              style={{
                display: "flex",
                flexDirection: "column",
                gap: 8,
                background: "var(--page)",
                borderRadius: 12,
                padding: "10px 14px",
                fontSize: 14,
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 12, width: "100%", flexWrap: "wrap" }}>
                <strong>{goal.label || `${goal.metric} target`}</strong>
                <span style={{ color: "var(--ink-muted)" }}>
                  {goal.target.toLocaleString()} {goal.metric} · {goal.start} to{" "}
                  {goal.end}
                </span>
                <button
                  onClick={() => remove(goal.id)}
                  disabled={busy}
                  style={{
                    marginLeft: "auto",
                    border: "none",
                    background: "transparent",
                    color: "var(--ink-light)",
                    fontSize: 13,
                  }}
                >
                  Remove
                </button>
              </div>

              {/* Where it actually stands. A target with no measurement beside
                  it is indistinguishable from a target nothing is tracking,
                  which is exactly how this panel read before. */}
              <GoalStanding progress={progressFor(goal.id)} target={goal.target} />
            </div>
          ))}
        </div>
      )}

      {open && (
        <motion.form
          initial={{ opacity: 0, height: 0 }}
          animate={{ opacity: 1, height: "auto" }}
          onSubmit={submit}
          style={{
            overflow: "hidden",
            display: "flex",
            flexWrap: "wrap",
            gap: 12,
            alignItems: "flex-end",
          }}
        >
          <Field label="Track">
            <select
              value={metric}
              onChange={(e) => setMetric(e.target.value as "revenue" | "profit")}
              style={inputStyle}
            >
              <option value="revenue">Revenue</option>
              {hasProfit && <option value="profit">Profit</option>}
            </select>
          </Field>

          <Field label="Target amount">
            <input
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              inputMode="decimal"
              placeholder="60000"
              style={{ ...inputStyle, width: 130 }}
            />
          </Field>

          <Field label="From">
            <input
              type="date"
              value={window.start}
              onChange={(e) => setWindow((w) => ({ ...w, start: e.target.value }))}
              style={inputStyle}
            />
          </Field>

          <Field label="To">
            <input
              type="date"
              value={window.end}
              onChange={(e) => setWindow((w) => ({ ...w, end: e.target.value }))}
              style={inputStyle}
            />
          </Field>

          <Field label="Name (optional)">
            <input
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="Q3 target"
              style={{ ...inputStyle, width: 140 }}
            />
          </Field>

          <button className="btn" type="submit" disabled={busy} style={{ fontSize: 14 }}>
            {busy ? "Saving…" : "Track it"}
          </button>

          {error && (
            <div style={{ flexBasis: "100%", color: "#c74722", fontSize: 13.5 }}>
              {error}
            </div>
          )}
        </motion.form>
      )}
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  fontFamily: "var(--font-body)",
  fontSize: 14,
  padding: "9px 12px",
  borderRadius: 10,
  border: "1.5px solid var(--line-strong)",
  background: "#fff",
  color: "var(--ink)",
};

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 5 }}>
      <span style={{ fontSize: 12, fontWeight: 600, color: "var(--ink-light)" }}>
        {label}
      </span>
      {children}
    </label>
  );
}


/**
 * What the engine actually measured against a target.
 *
 * The bar is drawn from banked-so-far against the target, with a marker where
 * the pace needed to be by now. That second mark is the point: a bar at 40%
 * means nothing until you know whether 40% was on schedule or behind.
 *
 * When there is nothing to measure - a window that starts after the data ends
 * is the common case - the sentence says so rather than the panel going quiet.
 */
function GoalStanding({
  progress,
  target,
}: {
  progress?: GoalProgress;
  target: number;
}) {
  if (!progress) {
    return (
      <div style={{ fontSize: 13, color: "var(--ink-light)" }}>
        Not measured yet - it is worked out when the analysis next runs.
      </div>
    );
  }

  const actual = Number(progress.facts?.actual ?? 0);
  const elapsed = Number(progress.facts?.elapsed ?? 0);
  const banked = target > 0 ? Math.min(actual / target, 1) : 0;
  const tone =
    progress.severity === "urgent"
      ? "var(--accent)"
      : progress.severity === "good"
        ? "#15866b"
        : "var(--ink-light)";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {target > 0 && elapsed > 0 && (
        <div
          style={{
            position: "relative",
            height: 8,
            borderRadius: 999,
            background: "#eae4d8",
            overflow: "visible",
          }}
        >
          <div
            style={{
              width: `${banked * 100}%`,
              height: "100%",
              borderRadius: 999,
              background: tone,
            }}
          />
          {/* Where the pace needed to be by now. */}
          <div
            title="Where you needed to be by now"
            style={{
              position: "absolute",
              left: `${Math.min(elapsed * 100, 100)}%`,
              top: -3,
              width: 2,
              height: 14,
              background: "var(--ink)",
              opacity: 0.55,
            }}
          />
        </div>
      )}

      <div style={{ fontSize: 13, lineHeight: 1.5, color: "var(--ink-muted)" }}>
        {progress.says}
      </div>
    </div>
  );
}
