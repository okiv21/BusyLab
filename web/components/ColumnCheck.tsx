"use client";

/**
 * The confirmation screen.
 *
 * It only ever shows columns the engine could not settle by itself (spec 3.2).
 * A clean file skips this screen entirely; a messy one is asked about its own
 * mess and nothing else, so effort scales with the user's own data quality.
 *
 * Locked tiers are shown rather than hidden, because "add a cost column to
 * unlock profit insights" teaches better data habits and demonstrates the
 * value sitting one column away (spec 3.4).
 */

import { useState } from "react";
import { motion } from "framer-motion";
import type { Columns } from "@/lib/types";

export default function ColumnCheck({
  columns,
  onConfirm,
  busy,
}: {
  columns: Columns;
  onConfirm: (roles: Record<string, string>) => void;
  busy: boolean;
}) {
  const [answers, setAnswers] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      columns.prompts
        .filter((p) => p.suggested)
        .map((p) => [p.column, p.suggested as string])
    )
  );

  const locked = columns.tiers.filter((t) => !t.unlocked && t.locked_prompt);

  return (
    <motion.section
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
      style={{ padding: "var(--pad-section)", display: "flex", flexDirection: "column", gap: 24 }}
    >
      <div>
        <h2 style={{ font: "700 26px var(--font-display)" }}>
          {columns.prompts.length === 0
            ? "We read your file. It all made sense."
            : "We read your file. Quick check."}
        </h2>
        <p style={{ margin: "8px 0 0", fontSize: 15.5, color: "var(--ink-muted)" }}>
          {/* loader.summary already leads with the row count, so it is not
              repeated here. */}
          {columns.loader.summary}
          {columns.prompts.length === 0
            ? " · everything matched automatically"
            : ` · ${columns.prompts.length} to check`}
          {columns.reused_mapping && " · matched a layout you've used before"}
        </p>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {columns.confirmed.map((c) => (
          <div
            key={c.role}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 14,
              background: "#fff",
              border: "1px solid #edf5f0",
              borderRadius: 14,
              padding: "14px 18px",
              flexWrap: "wrap",
            }}
          >
            <div
              style={{
                width: 26,
                height: 26,
                borderRadius: "50%",
                background: "var(--good-wash)",
                display: "grid",
                placeItems: "center",
                flex: "0 0 auto",
              }}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#1fa97a" strokeWidth="3" strokeLinecap="round">
                <path d="M20 6 9 17l-5-5" />
              </svg>
            </div>
            <span style={{ font: "600 15px var(--font-display)", minWidth: 110 }}>
              {c.label}
            </span>
            <span style={{ fontSize: 13.5, color: "var(--ink-light)", flex: 1 }}>
              from column &quot;{c.column}&quot; · {c.reason}
            </span>
            <span
              className="pill"
              style={{ color: "#177e5b", background: "var(--good-wash)" }}
            >
              Auto-detected
            </span>
          </div>
        ))}

        {columns.prompts.map((p) => (
          <div
            key={p.column}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 14,
              background: "#fffbf4",
              border: `1.5px solid ${answers[p.column] ? "var(--good-line)" : "var(--warn-line)"}`,
              borderRadius: 14,
              padding: "14px 18px",
              flexWrap: "wrap",
              transition: "border-color 0.3s",
            }}
          >
            <div
              style={{
                width: 26,
                height: 26,
                borderRadius: "50%",
                background: answers[p.column] ? "var(--good-wash)" : "var(--warn-wash)",
                display: "grid",
                placeItems: "center",
                font: "700 13px var(--font-display)",
                color: answers[p.column] ? "#177e5b" : "#b06a1e",
                flex: "0 0 auto",
              }}
            >
              {answers[p.column] ? "✓" : "?"}
            </div>
            <span style={{ font: "600 15px var(--font-display)", minWidth: 110 }}>
              {p.column}
            </span>
            <span style={{ fontSize: 14, color: "var(--ink-muted)", flex: 1, minWidth: 200 }}>
              {p.question}
            </span>
            <select
              value={answers[p.column] ?? ""}
              onChange={(e) =>
                setAnswers((a) => ({ ...a, [p.column]: e.target.value }))
              }
              style={{
                fontFamily: "var(--font-body)",
                fontSize: 14,
                fontWeight: 600,
                color: "var(--ink)",
                padding: "9px 12px",
                borderRadius: 10,
                border: "1.5px solid var(--warn)",
                background: "var(--warn-wash)",
              }}
            >
              <option value="">Choose…</option>
              {p.options.map((o) => (
                <option key={o.role} value={o.role}>
                  {o.label}
                </option>
              ))}
              {p.allow_group_by && (
                <option value="group_by">Compare results across it</option>
              )}
              {p.allow_ignore && <option value="ignore">Ignore this column</option>}
            </select>
          </div>
        ))}

        {locked.map((t) => (
          <div
            key={t.tier}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 14,
              background: "#f7f5f1",
              border: "1px dashed #ddd6cb",
              borderRadius: 14,
              padding: "14px 18px",
              opacity: 0.8,
            }}
          >
            <div
              style={{
                width: 26,
                height: 26,
                borderRadius: "50%",
                background: "#ece7de",
                display: "grid",
                placeItems: "center",
                flex: "0 0 auto",
              }}
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#8a8378" strokeWidth="2">
                <rect width="18" height="11" x="3" y="11" rx="2" />
                <path d="M7 11V7a5 5 0 0 1 10 0v4" />
              </svg>
            </div>
            <span style={{ fontSize: 14, color: "var(--ink-muted)", flex: 1 }}>
              {t.locked_prompt}
            </span>
            <span style={{ fontSize: 12, fontWeight: 600, color: "var(--ink-light)" }}>
              Locked
            </span>
          </div>
        ))}
      </div>

      {columns.missing.length > 0 && (
        <div
          role="alert"
          style={{
            background: "#fff3ec",
            border: "1.5px solid #f3d9cc",
            borderRadius: 14,
            padding: "14px 18px",
            fontSize: 14.5,
            color: "#c74722",
          }}
        >
          BusyLab needs {columns.missing.map((m) => m.label).join(", ")} to run.
          It could not find {columns.missing.length > 1 ? "those" : "that"} in
          this file.
        </div>
      )}

      <button
        className="btn"
        onClick={() => onConfirm(answers)}
        disabled={busy || columns.missing.length > 0}
        style={{ alignSelf: "center", fontSize: 16, padding: "15px 34px" }}
      >
        {busy ? "Building your story…" : "Looks right, build my story →"}
      </button>
    </motion.section>
  );
}
