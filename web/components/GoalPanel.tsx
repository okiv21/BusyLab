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
import type { Goal } from "@/lib/types";

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
  hasProfit,
  onChanged,
}: {
  datasetId: string;
  goals: Goal[];
  hasProfit: boolean;
  onChanged: () => void;
}) {
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
                alignItems: "center",
                gap: 12,
                background: "var(--page)",
                borderRadius: 12,
                padding: "10px 14px",
                fontSize: 14,
                flexWrap: "wrap",
              }}
            >
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
