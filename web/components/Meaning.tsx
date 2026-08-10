"use client";

/**
 * The plain-language half of a finding.
 *
 * The summary states what is true and assumes the reader knows why that is the
 * thing being measured. This shows what it means. Both are needed: the summary
 * without the meaning is a correct sentence nobody can act on, and the meaning
 * without the summary is a generality with no numbers behind it.
 *
 * Presented quietly rather than as a callout box. A reader who already follows
 * the summary should be able to skim past this, and a reader who does not
 * should find it exactly where they got stuck - directly underneath.
 */

import { useState } from "react";
import type { Finding } from "@/lib/types";

/** Wrap glossary terms in the summary so a definition is one tap away. */
function withGlossary(text: string, glossary: Record<string, string>) {
  const terms = Object.keys(glossary).sort((a, b) => b.length - a.length);
  if (terms.length === 0) return text;

  // Longest first, so "average order value" is matched before "value" inside
  // it. Word-bounded, or "cost" matches inside "costs" and splits the word in
  // half on screen, and "profit" turns "profitable" into "profit" + "able".
  // A trailing plural is absorbed into the match rather than left dangling.
  const pattern = new RegExp(
    `\\b(${terms
      .map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
      .join("|")})(s?)\\b`,
    "gi"
  );

  // Capture groups mean split() yields [text, term, plural, text, ...], so the
  // plural fragment is rejoined onto its term instead of becoming a stray "s".
  const parts = text.split(pattern);
  return parts.map((part, index) => {
    // Every third element from index 2 is a captured plural suffix, already
    // shown as part of the preceding term.
    if (index % 3 === 2) return null;
    const definition = glossary[part.toLowerCase()];
    if (!definition) return <span key={index}>{part}</span>;
    const plural = parts[index + 1] ?? "";
    return (
      <abbr
        key={index}
        title={definition}
        style={{
          textDecoration: "none",
          borderBottom: "1px dotted var(--ink-light)",
          cursor: "help",
        }}
      >
        {part}
        {plural}
      </abbr>
    );
  });
}

export function Summary({ finding }: { finding: Finding }) {
  const glossary = finding.glossary ?? {};
  return <>{withGlossary(finding.summary, glossary)}</>;
}

export default function Meaning({ finding }: { finding: Finding }) {
  const [open, setOpen] = useState(false);
  if (!finding.meaning) return null;

  return (
    <div style={{ marginTop: -4 }}>
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        style={{
          border: "none",
          background: "transparent",
          padding: 0,
          fontSize: 13,
          color: "var(--accent)",
          fontWeight: 600,
          display: "flex",
          alignItems: "center",
          gap: 5,
        }}
      >
        <span
          aria-hidden
          style={{
            display: "inline-block",
            transition: "transform 140ms ease",
            transform: open ? "rotate(90deg)" : "none",
          }}
        >
          ›
        </span>
        {open ? "Hide what this means" : "What does this mean?"}
      </button>

      {open && (
        <p
          style={{
            margin: "8px 0 0",
            fontSize: 14.5,
            lineHeight: 1.6,
            color: "var(--ink-muted)",
            borderLeft: "2px solid var(--accent-wash)",
            paddingLeft: 12,
          }}
        >
          {finding.meaning}
        </p>
      )}
    </div>
  );
}

/**
 * Which findings an answer was built from.
 *
 * Shown because the answering layer cannot mechanically rule out one failure:
 * a true, verifiable, irrelevant fact placed next to the question so that
 * proximity implies a connection. Nothing in such an answer is false, so no
 * check rejects it. A reader who can see the sources can see when they do not
 * add up, which is the only defence left.
 */
export function AnswerSources({
  sources,
  generated,
}: {
  sources?: string[];
  generated?: boolean;
}) {
  if (!sources || sources.length === 0) return null;
  return (
    <div
      style={{
        marginTop: 10,
        fontSize: 12,
        color: "var(--ink-light)",
        display: "flex",
        flexWrap: "wrap",
        gap: 6,
        alignItems: "center",
      }}
    >
      <span>{generated ? "Built from" : "From"}</span>
      {sources.map((id) => (
        <code
          key={id}
          style={{
            fontSize: 11.5,
            background: "var(--accent-wash)",
            padding: "2px 7px",
            borderRadius: "var(--radius-pill)",
            color: "#9a3d1f",
          }}
        >
          {id.replace(/_/g, " ")}
        </code>
      ))}
    </div>
  );
}
