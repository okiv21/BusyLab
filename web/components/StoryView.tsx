"use client";

/**
 * The story: a ranked vertical narrative, most important first.
 *
 * Not a grid and not a dashboard builder (spec 6). The user never picks a
 * chart, an axis or a filter; they read a result the engine ranked and the
 * design laid out. Interactivity only ever goes *deeper into* findings the
 * engine already produced, never sideways into doing the analysis themselves.
 */

import { useState } from "react";
import { motion } from "framer-motion";
import FindingCard from "./FindingCard";
import { ask } from "@/lib/api";
import type { Answer, Chip, Finding, Story } from "@/lib/types";

export default function StoryView({
  story,
  datasetId,
}: {
  story: Story;
  datasetId: string;
}) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [thinking, setThinking] = useState(false);
  const [activeChip, setActiveChip] = useState<string | null>(null);

  const send = async (text: string, chip?: Chip) => {
    if (!text.trim() || thinking) return;
    setThinking(true);
    setActiveChip(chip?.name ?? null);
    try {
      setAnswer(await ask(datasetId, text));
    } catch (err) {
      setAnswer({
        answered: false,
        message: err instanceof Error ? err.message : "That didn't work.",
      });
    } finally {
      setThinking(false);
    }
  };

  const [hero, ...rest] = story.findings;
  const locked = story.locked ?? [];

  return (
    <section
      style={{
        padding: "40px 60px 48px",
        display: "flex",
        flexDirection: "column",
        gap: 22,
      }}
    >
      <header>
        <div className="eyebrow">analysed just now</div>
        <h2 style={{ font: "700 27px var(--font-display)", margin: "6px 0" }}>
          Here&apos;s your story.
        </h2>
        <p style={{ margin: 0, fontSize: 15, color: "var(--ink-muted)" }}>
          {story.findings.length}{" "}
          {story.findings.length === 1 ? "finding" : "findings"}, ranked by how
          much they matter. Start at the top.
        </p>
      </header>

      {story.findings.length === 0 && (
        <div className="card" style={{ padding: 28, fontSize: 15.5 }}>
          Nothing in this data stood out as unusual. That is itself a finding:
          the ups and downs sit inside your normal variation.
        </div>
      )}

      {hero && <FindingCard finding={hero} index={0} hero />}
      {rest.map((f, i) => (
        <FindingCard key={f.id} finding={f} index={i + 1} />
      ))}

      {/* --- drill-down: guided, on rails ------------------------------- */}
      {story.findings.length > 0 && (
        <div
          className="card"
          style={{
            padding: "22px 26px",
            display: "flex",
            flexDirection: "column",
            gap: 14,
            background: "var(--card)",
          }}
        >
          <div style={{ font: "600 13.5px var(--font-display)", color: "var(--ink-muted)" }}>
            Keep pulling the thread
          </div>

          <div style={{ display: "flex", flexWrap: "wrap", gap: 9 }}>
            {story.chips?.map((chip) => {
              const active = activeChip === chip.name;
              return (
                <button
                  key={chip.name}
                  onClick={() => {
                    setQuestion(chip.label);
                    send(chip.label, chip);
                  }}
                  style={{
                    padding: "10px 17px",
                    borderRadius: "var(--radius-pill)",
                    fontSize: 14,
                    fontWeight: 600,
                    color: active ? "#fff" : "var(--ink-muted)",
                    background: active ? "var(--accent)" : "#fff",
                    border: `1.5px solid ${active ? "var(--accent)" : "var(--line-strong)"}`,
                    transition: "all 0.25s",
                  }}
                >
                  {chip.label}
                </button>
              );
            })}
          </div>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              send(question);
            }}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              background: "#fff",
              border: "1.5px solid var(--line-strong)",
              borderRadius: "var(--radius-pill)",
              padding: "7px 8px 7px 18px",
            }}
          >
            <input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="Ask anything about these findings…"
              aria-label="Ask a question about these findings"
              style={{
                flex: 1,
                border: "none",
                outline: "none",
                background: "transparent",
                fontFamily: "var(--font-body)",
                fontSize: 15,
                color: "var(--ink)",
              }}
            />
            <button
              type="submit"
              className="btn"
              disabled={thinking || !question.trim()}
              style={{ padding: "10px 20px", fontSize: 13.5, boxShadow: "none" }}
            >
              {thinking ? "…" : "Ask"}
            </button>
          </form>

          {answer && (
            <motion.div
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.3 }}
              style={{
                background: "var(--accent-soft)",
                border: "1.5px solid #f3d9cc",
                borderRadius: 16,
                padding: "18px 20px",
                display: "flex",
                gap: 12,
              }}
            >
              <div
                style={{
                  flex: "0 0 auto",
                  width: 30,
                  height: 30,
                  borderRadius: 10,
                  background: "var(--accent)",
                  display: "grid",
                  placeItems: "center",
                  color: "#fff",
                  font: "800 14px var(--font-display)",
                }}
              >
                B
              </div>
              <div style={{ fontSize: 15, lineHeight: 1.55 }}>
                {answer.answered ? (
                  <>
                    <div>{answer.answer}</div>
                    {answer.route && (
                      <div
                        style={{
                          marginTop: 8,
                          fontSize: 12.5,
                          color: "var(--ink-light)",
                        }}
                      >
                        answered from “{answer.route.label}”
                      </div>
                    )}
                  </>
                ) : (
                  <>
                    <div>{answer.message}</div>
                    {answer.suggestions && answer.suggestions.length > 0 && (
                      <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
                        {answer.suggestions.map((s) => (
                          <button
                            key={s.name}
                            onClick={() => {
                              setQuestion(s.label);
                              send(s.label, s);
                            }}
                            style={{
                              padding: "7px 13px",
                              borderRadius: "var(--radius-pill)",
                              fontSize: 13,
                              fontWeight: 600,
                              color: "var(--accent)",
                              background: "#fff",
                              border: "1.5px solid #f3d9cc",
                            }}
                          >
                            {s.label}
                          </button>
                        ))}
                      </div>
                    )}
                  </>
                )}
              </div>
            </motion.div>
          )}
        </div>
      )}

      {/* --- what one more column would buy ----------------------------- */}
      {locked.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div className="eyebrow">Sitting one column away</div>
          {locked.map((l) => (
            <div
              key={l.tier}
              style={{
                background: "#f7f5f1",
                border: "1px dashed #ddd6cb",
                borderRadius: 14,
                padding: "13px 18px",
                fontSize: 14,
                color: "var(--ink-muted)",
              }}
            >
              {l.prompt}
            </div>
          ))}
        </div>
      )}

      {story.notes?.length > 0 && (
        <details style={{ fontSize: 13, color: "var(--ink-light)" }}>
          <summary style={{ cursor: "pointer" }}>What BusyLab did to your file</summary>
          <ul style={{ marginTop: 8, lineHeight: 1.7 }}>
            {story.notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
