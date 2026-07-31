"use client";

/**
 * The flow after upload: check columns → analysing → story.
 *
 * These are stages of one dataset rather than separate destinations, so they
 * share a route and swap the body. Analysis runs as a background job, so the
 * analysing stage reports the worker's own step text rather than an invented
 * progress bar.
 */

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { motion } from "framer-motion";
import { AppHeader, Stepper } from "@/components/Brand";
import ColumnCheck from "@/components/ColumnCheck";
import StoryView from "@/components/StoryView";
import { ApiError, confirmColumns, getColumns, getStory, waitForJob } from "@/lib/api";
import type { Columns, Story } from "@/lib/types";

type Stage = "loading" | "columns" | "analysing" | "story" | "error";

export default function DatasetPage() {
  const { id } = useParams<{ id: string }>();
  const [stage, setStage] = useState<Stage>("loading");
  const [columns, setColumns] = useState<Columns | null>(null);
  const [story, setStory] = useState<Story | null>(null);
  const [step, setStep] = useState("");
  const [error, setError] = useState<string | null>(null);

  // An already-analysed dataset should reopen on its story, not start over.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const existing = await getStory(id).catch(() => null);
        if (cancelled) return;
        if (existing) {
          setStory(existing);
          setStage("story");
          return;
        }
        const cols = await getColumns(id);
        if (cancelled) return;
        setColumns(cols);
        setStage("columns");
      } catch (err) {
        if (cancelled) return;
        setError(
          err instanceof ApiError && err.status === 409
            ? "This file is still being read. Give it a moment and refresh."
            : err instanceof Error
              ? err.message
              : "Something went wrong."
        );
        setStage("error");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id]);

  const confirm = useCallback(
    async (roles: Record<string, string>) => {
      setStage("analysing");
      setStep("cleaning up the rows");
      try {
        const { job_id } = await confirmColumns(id, roles);
        const job = await waitForJob(job_id, (j) => setStep(j.step || ""));
        if (job.status === "failed") {
          setError(job.error ?? "The analysis could not finish.");
          setStage("error");
          return;
        }
        setStory(await getStory(id));
        setStage("story");
      } catch (err) {
        setError(err instanceof Error ? err.message : "Something went wrong.");
        setStage("error");
      }
    },
    [id]
  );

  const stepIndex: 0 | 1 | 2 =
    stage === "columns" ? 1 : stage === "story" ? 2 : stage === "analysing" ? 2 : 1;

  return (
    <main className="shell">
      <div className="frame">
        <AppHeader>
          {stage === "story" ? <span /> : <Stepper active={stepIndex} />}
          <span />
        </AppHeader>

        {stage === "loading" && <Centered>Opening your file…</Centered>}

        {stage === "columns" && columns && (
          <ColumnCheck columns={columns} onConfirm={confirm} busy={false} />
        )}

        {stage === "analysing" && <Analysing step={step} />}

        {stage === "story" && story && <StoryView story={story} datasetId={id} />}

        {stage === "error" && (
          <Centered>
            <div
              role="alert"
              style={{
                background: "#fff3ec",
                border: "1.5px solid #f3d9cc",
                borderRadius: 14,
                padding: "16px 20px",
                color: "#c74722",
                fontSize: 15,
                maxWidth: 520,
              }}
            >
              {error}
            </div>
          </Centered>
        )}
      </div>
    </main>
  );
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        padding: "110px 60px 130px",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 20,
        color: "var(--ink-muted)",
      }}
    >
      {children}
    </div>
  );
}

/**
 * Real stages from the worker, not a fake percentage.
 *
 * Steps already passed are ticked; the current one spins. If the worker is
 * between named steps the list simply does not advance, which is honest.
 */
function Analysing({ step }: { step: string }) {
  const stages = [
    "cleaning up the rows",
    "checking against normal variation",
    "ranking what matters most",
  ];
  const current = Math.max(0, stages.indexOf(step));

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      style={{
        padding: "110px 60px 130px",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 30,
      }}
    >
      <div
        style={{
          width: 60,
          height: 60,
          borderRadius: 20,
          background: "var(--accent-wash)",
          display: "grid",
          placeItems: "center",
        }}
      >
        <svg width="27" height="27" viewBox="0 0 24 24" fill="none" stroke="#e85a32" strokeWidth="2" strokeLinecap="round">
          <circle cx="11" cy="11" r="7" />
          <path d="m21 21-4.3-4.3" />
        </svg>
      </div>
      <div style={{ font: "700 24px var(--font-display)" }}>Reading your business…</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 14, minWidth: 300 }}>
        {stages.map((label, i) => (
          <div key={label} style={{ display: "flex", alignItems: "center", gap: 12 }}>
            {i < current ? (
              <Tick />
            ) : i === current ? (
              <div
                style={{
                  width: 22,
                  height: 22,
                  borderRadius: "50%",
                  border: "2.5px solid var(--accent)",
                  borderTopColor: "transparent",
                  animation: "spin 0.9s linear infinite",
                }}
              />
            ) : (
              <div
                style={{
                  width: 22,
                  height: 22,
                  borderRadius: "50%",
                  border: "2px solid #e5dfd5",
                }}
              />
            )}
            <span
              style={{
                fontSize: 15,
                fontWeight: i <= current ? 600 : 400,
                color: i <= current ? "var(--ink)" : "var(--ink-light)",
              }}
            >
              {label}
            </span>
          </div>
        ))}
      </div>
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </motion.div>
  );
}

function Tick() {
  return (
    <div
      style={{
        width: 22,
        height: 22,
        borderRadius: "50%",
        background: "var(--good-wash)",
        display: "grid",
        placeItems: "center",
      }}
    >
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#1fa97a" strokeWidth="3" strokeLinecap="round">
        <path d="M20 6 9 17l-5-5" />
      </svg>
    </div>
  );
}
