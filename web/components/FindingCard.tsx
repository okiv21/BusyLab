"use client";

/**
 * One finding: a visual, a plain English sentence, and its receipts.
 *
 * The first card gets the hero treatment and the rest stay calm. Spec 7 is
 * explicit that there is one hero moment per story: if everything shouts,
 * the biggest insight does not land.
 *
 * The evidence line is deliberately always available. "Revenue is down 18%"
 * is a claim, and being able to see the test behind it in one click is what
 * separates this from a dashboard that asserts things.
 */

import { useState } from "react";
import { motion } from "framer-motion";
import Chart from "./charts/Chart";
import type { Finding } from "@/lib/types";
import { percent } from "@/lib/format";
import { entrance, useCountUp, usePrefersReducedMotion } from "@/lib/motion";

const TONE: Record<
  string,
  { label: string; fg: string; bg: string }
> = {
  urgent: { label: "Needs your attention", fg: "#c74722", bg: "#fbe3d8" },
  watch: { label: "Worth knowing", fg: "#b06a1e", bg: "#fdf6ec" },
  good: { label: "Going well", fg: "#177e5b", bg: "#e7f6f0" },
  neutral: { label: "Context", fg: "#6e675c", bg: "#f0ede7" },
};

export default function FindingCard({
  finding,
  index,
  hero = false,
  onDrill,
}: {
  finding: Finding;
  index: number;
  hero?: boolean;
  onDrill?: (finding: Finding) => void;
}) {
  const [showEvidence, setShowEvidence] = useState(false);
  const reduced = usePrefersReducedMotion();
  const tone = TONE[finding.severity] ?? TONE.neutral;

  return (
    <motion.article
      {...entrance(index, reduced)}
      style={{
        background: hero
          ? "linear-gradient(180deg, #fff8f4, #fff3ec)"
          : "var(--white)",
        border: hero ? "1.5px solid #f3d9cc" : "1px solid var(--line)",
        borderRadius: hero ? "var(--radius-hero)" : "var(--radius-card)",
        // One hero moment per story (spec 7). The glow is the flair, and it
        // appears on exactly one card so the rest stays calm and it lands.
        boxShadow: hero
          ? "var(--shadow-hero), 0 0 0 1px rgba(232,90,50,0.08), 0 0 60px -12px rgba(232,90,50,0.28)"
          : "var(--shadow-card)",
        padding: hero ? "28px 30px" : "24px 28px",
        display: "flex",
        flexDirection: "column",
        gap: 15,
      }}
    >
      <header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 14,
          flexWrap: "wrap",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {hero ? (
            <span
              className="pill"
              style={{
                fontFamily: "var(--font-display)",
                fontWeight: 700,
                fontSize: 13,
                color: "#c74722",
                background: "#fbe3d8",
                padding: "5px 12px",
              }}
            >
              FINDING 1
            </span>
          ) : (
            <span
              style={{
                fontFamily: "var(--font-display)",
                fontWeight: 700,
                fontSize: 14,
                color: "var(--ink-faint)",
              }}
            >
              {String(index + 1).padStart(2, "0")}
            </span>
          )}
          <span style={{ fontSize: 13, fontWeight: 600, color: tone.fg }}>
            {tone.label}
          </span>
        </div>

        {onDrill && (
          <button
            onClick={() => onDrill(finding)}
            style={{
              border: "none",
              background: "transparent",
              color: "var(--accent)",
              fontWeight: 600,
              fontSize: 13.5,
              padding: 0,
            }}
          >
            Dig in →
          </button>
        )}
      </header>

      {hero ? <HeroHeadline finding={finding} /> : (
        <h3
          style={{
            font: "600 19px/1.4 var(--font-display)",
            letterSpacing: "-0.01em",
          }}
        >
          {finding.summary}
        </h3>
      )}

      <Chart finding={finding} />

      <footer style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <button
          onClick={() => setShowEvidence((v) => !v)}
          style={{
            alignSelf: "flex-start",
            border: "none",
            background: "transparent",
            padding: 0,
            fontSize: 12.5,
            color: "var(--ink-light)",
            display: "flex",
            alignItems: "center",
            gap: 6,
          }}
        >
          <span
            style={{
              width: 7,
              height: 7,
              borderRadius: "50%",
              background:
                finding.evidence.strength === "strong" ||
                finding.evidence.strength === "clear"
                  ? "var(--good)"
                  : "var(--ink-faint)",
            }}
          />
          {finding.evidence.strength} · how do we know?
        </button>

        {showEvidence && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            style={{
              overflow: "hidden",
              fontSize: 13,
              lineHeight: 1.6,
              color: "var(--ink-muted)",
              background: "var(--page)",
              borderRadius: 12,
              padding: "12px 14px",
            }}
          >
            <div>
              <strong>Method:</strong> {finding.evidence.method}
            </div>
            {finding.evidence.sample_size != null && (
              <div>
                <strong>Based on:</strong> {finding.evidence.sample_size} data points
              </div>
            )}
            {finding.evidence.p_value != null && (
              <div>
                <strong>Likelihood this is chance:</strong>{" "}
                {percent(finding.evidence.p_value, 2)}
                {finding.evidence.adjusted_p != null &&
                  ` (${percent(finding.evidence.adjusted_p, 2)} after accounting for how many comparisons were made)`}
              </div>
            )}
            {finding.evidence.correction && (
              <div>
                <strong>Correction:</strong> {finding.evidence.correction}
              </div>
            )}
            {finding.evidence.notes?.map((note) => (
              <div key={note} style={{ marginTop: 4 }}>
                {note}
              </div>
            ))}
          </motion.div>
        )}
      </footer>
    </motion.article>
  );
}

/** The hero gets the one big number in the story, if it has one to give. */
function HeroHeadline({ finding }: { finding: Finding }) {
  const big = pickHeadlineNumber(finding);
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 28,
        flexWrap: "wrap",
      }}
    >
      {big && (
        <div style={{ flex: "0 0 auto" }}>
          <CountingNumber
            magnitude={big.magnitude}
            render={big.render}
            tone={big.tone}
          />
          <div
            style={{
              fontSize: 13,
              fontWeight: 600,
              color: "var(--ink-light)",
              marginTop: 4,
            }}
          >
            {big.caption}
          </div>
        </div>
      )}
      <h2
        style={{
          font: "600 22px/1.35 var(--font-display)",
          letterSpacing: "-0.01em",
          flex: 1,
          minWidth: 260,
        }}
      >
        {finding.summary}
      </h2>
    </div>
  );
}

/**
 * The headline number, counted up (spec 7: "numbers count up").
 *
 * The count is driven from the raw magnitude and formatted every frame, rather
 * than animating a pre-formatted string, so "-44%" arrives by counting through
 * the percentages instead of scrambling characters.
 */
function CountingNumber({
  magnitude,
  render,
  tone,
}: {
  magnitude: number;
  render: (value: number) => string;
  tone: string;
}) {
  const value = useCountUp(magnitude);
  return (
    <div
      style={{
        font: "800 58px/1 var(--font-display)",
        letterSpacing: "-0.03em",
        color: tone,
        fontVariantNumeric: "tabular-nums",
      }}
    >
      {render(value)}
    </div>
  );
}

/**
 * Pull the one number worth setting large.
 *
 * Reads only from facts the engine computed. If a finding has no single
 * headline number, none is invented and the sentence carries the card.
 * `magnitude` is the raw value so it can be counted; `render` formats it.
 */
function pickHeadlineNumber(finding: Finding): {
  magnitude: number;
  render: (value: number) => string;
  caption: string;
  tone: string;
} | null {
  const f = finding.facts ?? {};

  if (typeof f.change_pct === "number") {
    const sign = f.change_pct < 0 ? "−" : "+";
    return {
      magnitude: Math.abs(f.change_pct),
      render: (v) => `${sign}${percent(v)}`,
      caption: `revenue, ${f.periods ?? ""} periods`.trim(),
      tone: f.change_pct < 0 ? "#c74722" : "#177e5b",
    };
  }
  if (typeof f.top1_share === "number") {
    return {
      magnitude: f.top1_share,
      render: (v) => percent(v),
      caption: `of ${f.metric ?? "revenue"} from one product`,
      tone: "#c74722",
    };
  }
  if (typeof f.margin === "number") {
    return {
      magnitude: Math.abs(f.margin),
      render: (v) => percent(v),
      caption: "margin",
      tone: f.margin < 0 ? "#c74722" : "#177e5b",
    };
  }
  if (typeof f.driver_share_of_change === "number") {
    return {
      magnitude: f.driver_share_of_change,
      render: (v) => percent(v),
      caption: `of the move is ${f.driver ?? "one slice"}`,
      tone: "#c74722",
    };
  }
  return null;
}
