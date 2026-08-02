"use client";

/**
 * What the user sees when the data quality gate holds an analysis.
 *
 * Spec 4.3: a refresh that fails the gate holds the analysis and raises a flag
 * rather than publishing a confidently wrong insight. The screen therefore has
 * to be genuinely useful, not an apology — the user needs to know what is
 * wrong with the file well enough to go and fix it.
 */

import { motion } from "framer-motion";
import type { Quality, QualityIssue } from "@/lib/types";

const TONE: Record<string, { fg: string; bg: string; line: string; label: string }> = {
  block: { fg: "#c74722", bg: "#fff3ec", line: "#f3d9cc", label: "Stops the analysis" },
  warn: { fg: "#b06a1e", bg: "#fdf6ec", line: "#f3e3c8", label: "Worth checking" },
  info: { fg: "#6e675c", bg: "#f0ede7", line: "#e2dcd2", label: "Noted" },
};

export default function QualityHold({
  quality,
  onRetry,
}: {
  quality: Quality;
  onRetry?: () => void;
}) {
  const blocking = quality.issues.filter((i) => i.severity === "block");
  const rest = quality.issues.filter((i) => i.severity !== "block");

  return (
    <motion.section
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
      style={{
        padding: "48px 60px 60px",
        display: "flex",
        flexDirection: "column",
        gap: 22,
      }}
    >
      <div style={{ display: "flex", alignItems: "flex-start", gap: 16 }}>
        <div
          style={{
            width: 44,
            height: 44,
            borderRadius: 14,
            background: "#fff3ec",
            display: "grid",
            placeItems: "center",
            flex: "0 0 auto",
          }}
        >
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#c74722" strokeWidth="2.2" strokeLinecap="round">
            <path d="M12 9v4" />
            <path d="M12 17h.01" />
            <circle cx="12" cy="12" r="10" />
          </svg>
        </div>
        <div>
          <h2 style={{ font: "700 25px var(--font-display)" }}>
            We&apos;ve paused before showing you anything.
          </h2>
          <p
            style={{
              margin: "8px 0 0",
              fontSize: 15.5,
              lineHeight: 1.55,
              color: "var(--ink-muted)",
              maxWidth: 620,
            }}
          >
            Something about this file would have made the findings wrong, and a
            wrong finding looks exactly as confident as a right one. Here is what
            we noticed.
          </p>
        </div>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {blocking.map((issue) => (
          <IssueCard key={issue.code} issue={issue} />
        ))}
        {rest.map((issue) => (
          <IssueCard key={issue.code} issue={issue} />
        ))}
      </div>

      {onRetry && (
        <button
          className="btn"
          onClick={onRetry}
          style={{ alignSelf: "flex-start" }}
        >
          I&apos;ve fixed it, try again
        </button>
      )}

      <p style={{ fontSize: 13, color: "var(--ink-light)", margin: 0 }}>
        Your file is untouched. Nothing was analysed and nothing was saved as a
        finding.
      </p>
    </motion.section>
  );
}

function IssueCard({ issue }: { issue: QualityIssue }) {
  const tone = TONE[issue.severity] ?? TONE.info;
  return (
    <div
      style={{
        background: tone.bg,
        border: `1.5px solid ${tone.line}`,
        borderRadius: 16,
        padding: "16px 20px",
        display: "flex",
        flexDirection: "column",
        gap: 6,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <span
          className="pill"
          style={{
            color: tone.fg,
            background: "#fff",
            border: `1px solid ${tone.line}`,
            fontSize: 11,
            letterSpacing: "0.04em",
            textTransform: "uppercase",
          }}
        >
          {tone.label}
        </span>
        <strong style={{ fontSize: 15.5, color: "var(--ink)" }}>{issue.title}</strong>
      </div>
      <div style={{ fontSize: 14.5, lineHeight: 1.55, color: "var(--ink-muted)" }}>
        {issue.detail}
      </div>
      {issue.sample.length > 0 && (
        <div style={{ fontSize: 13, color: "var(--ink-light)" }}>
          For example: {issue.sample.join(", ")}
        </div>
      )}
    </div>
  );
}
