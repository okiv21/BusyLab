"use client";

/**
 * Present mode: the story played in sequence, boardroom ready (spec Pillar 6).
 *
 * Almost free to build, because every ingredient already exists - ranked
 * findings, plain-English sentences, animated charts. This is those pieces
 * played one at a time with timing and transitions. No render pipeline, no
 * video files, never goes stale, and it updates the instant the data does.
 *
 * Rendered MP4 is explicitly rejected in the spec for exactly the reason this
 * component exists: a video is frozen the moment the numbers move.
 *
 * On the voiceover: the spec defers narration to a paid tier, assuming a hosted
 * TTS bill. The browser's own speech synthesiser has no bill and no API key, so
 * it is simply on for everyone. Availability is still checked, because Linux
 * browsers often ship with no voices installed.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import Chart from "./charts/Chart";
import { Logo } from "./Brand";
import type { Finding, Story } from "@/lib/types";

/** How long a slide holds when playing hands-free. */
const AUTO_ADVANCE_MS = 9000;

const TONE: Record<string, string> = {
  urgent: "#c74722",
  watch: "#b06a1e",
  good: "#177e5b",
  neutral: "#6e675c",
};

export default function PresentMode({
  story,
  datasetId,
  onExit,
}: {
  story: Story;
  datasetId: string;
  onExit: () => void;
}) {
  const slides = useMemo(
    () => story.findings.filter((f) => f.chart !== "callout"),
    [story.findings]
  );
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [voiceAvailable, setVoiceAvailable] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const current: Finding | undefined = slides[index];
  const last = index >= slides.length - 1;

  // Voices load asynchronously in most browsers, so this can fire late.
  useEffect(() => {
    if (typeof window === "undefined" || !("speechSynthesis" in window)) return;
    const check = () => setVoiceAvailable(speechSynthesis.getVoices().length > 0);
    check();
    speechSynthesis.addEventListener("voiceschanged", check);
    return () => speechSynthesis.removeEventListener("voiceschanged", check);
  }, []);

  const stopSpeaking = useCallback(() => {
    if (typeof window !== "undefined" && "speechSynthesis" in window) {
      speechSynthesis.cancel();
    }
    setSpeaking(false);
  }, []);

  const speak = useCallback(
    (text: string) => {
      if (!voiceAvailable || typeof window === "undefined") return;
      speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.rate = 0.95;
      utterance.pitch = 1;
      utterance.onend = () => setSpeaking(false);
      utterance.onerror = () => setSpeaking(false);
      setSpeaking(true);
      speechSynthesis.speak(utterance);
    },
    [voiceAvailable]
  );

  const go = useCallback(
    (next: number) => {
      stopSpeaking();
      setIndex(Math.max(0, Math.min(next, slides.length - 1)));
    },
    [slides.length, stopSpeaking]
  );

  // Auto-advance, paused on the final slide so a presentation ends rather
  // than looping behind the presenter.
  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    if (!playing || last) return;
    timer.current = setTimeout(() => setIndex((i) => i + 1), AUTO_ADVANCE_MS);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [playing, index, last]);

  useEffect(() => {
    if (playing && last) setPlaying(false);
  }, [playing, last]);

  // Keyboard control, because nobody presents with a mouse.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "ArrowRight" || event.key === " ") {
        event.preventDefault();
        go(index + 1);
      } else if (event.key === "ArrowLeft") {
        go(index - 1);
      } else if (event.key === "Escape") {
        stopSpeaking();
        onExit();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [go, index, onExit, stopSpeaking]);

  useEffect(() => stopSpeaking, [stopSpeaking]);

  if (!current) {
    return (
      <div style={{ padding: "80px 40px", textAlign: "center", color: "var(--ink-muted)" }}>
        There are no findings to present yet.
        <div style={{ marginTop: 18 }}>
          <button className="btn btn-ghost" onClick={onExit}>
            Back to the story
          </button>
        </div>
      </div>
    );
  }

  const base = process.env.NEXT_PUBLIC_API ?? "http://127.0.0.1:8000";

  return (
    <div
      style={{
        background: "linear-gradient(180deg, #fdfcfa 0%, #fbf6f1 100%)",
        minHeight: 700,
        display: "flex",
        flexDirection: "column",
        padding: "24px 40px 20px",
      }}
    >
      <header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 16,
          flexWrap: "wrap",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
          <Logo size={24} />
          <span style={{ font: "700 14px var(--font-display)" }}>BusyLab</span>
          <span
            style={{
              font: "600 12px var(--font-display)",
              color: "var(--ink-faint)",
              letterSpacing: "0.08em",
              textTransform: "uppercase",
              marginLeft: 6,
              paddingLeft: 14,
              borderLeft: "1px solid #e5dfd5",
            }}
          >
            Board view
          </span>
        </div>
        <button className="btn btn-ghost" onClick={onExit} style={{ fontSize: 13 }}>
          Exit ✕
        </button>
      </header>

      <div
        style={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: 20,
          textAlign: "center",
          padding: "26px 20px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ width: 26, height: 2, background: "var(--accent)", borderRadius: 2 }} />
          <span
            style={{
              font: "600 12.5px var(--font-display)",
              letterSpacing: "0.16em",
              textTransform: "uppercase",
              color: TONE[current.severity] ?? "var(--ink-muted)",
            }}
          >
            Finding {index + 1} of {slides.length}
          </span>
          <span style={{ width: 26, height: 2, background: "var(--accent)", borderRadius: 2 }} />
        </div>

        <AnimatePresence mode="wait">
          <motion.div
            key={current.id}
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -12 }}
            transition={{ duration: 0.42, ease: [0.16, 1, 0.3, 1] }}
            style={{ width: "100%", display: "flex", flexDirection: "column", gap: 20, alignItems: "center" }}
          >
            <h2
              style={{
                font: "700 34px/1.28 var(--font-display)",
                letterSpacing: "-0.025em",
                maxWidth: 860,
                textWrap: "balance",
              }}
            >
              {current.summary}
            </h2>

            <div
              className="card"
              style={{
                width: "100%",
                maxWidth: 880,
                padding: "28px 32px",
                boxShadow: "0 24px 60px rgba(33,28,21,0.09)",
              }}
            >
              <Chart finding={current} />
            </div>

            <div style={{ fontSize: 13, color: "var(--ink-light)" }}>
              {current.evidence.method} · {current.evidence.strength}
            </div>
          </motion.div>
        </AnimatePresence>
      </div>

      <footer
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 14,
          background: "#fff",
          border: "1px solid var(--line)",
          borderRadius: "var(--radius-pill)",
          padding: "8px 10px",
          boxShadow: "0 10px 26px rgba(33,28,21,0.06)",
          flexWrap: "wrap",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <RoundButton onClick={() => go(index - 1)} disabled={index === 0} label="Previous slide">
            ‹
          </RoundButton>
          <RoundButton onClick={() => go(index + 1)} disabled={last} primary label="Next slide">
            ›
          </RoundButton>

          <button
            className="btn btn-ghost"
            onClick={() => setPlaying((v) => !v)}
            disabled={last}
            style={{ fontSize: 13, padding: "9px 16px", marginLeft: 6 }}
          >
            {playing ? "Pause" : "Play"}
          </button>

          <div style={{ display: "flex", gap: 7, marginLeft: 10 }}>
            {slides.map((slide, i) => (
              <button
                key={slide.id}
                onClick={() => go(i)}
                aria-label={`Go to finding ${i + 1}`}
                style={{
                  width: 8,
                  height: 8,
                  padding: 0,
                  borderRadius: "50%",
                  border: "none",
                  background: i === index ? "var(--accent)" : "#e5dfd5",
                  transition: "background 0.3s",
                }}
              />
            ))}
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <a
            className="btn btn-ghost"
            href={`${base}/datasets/${datasetId}/export.pdf`}
            style={{ fontSize: 13, padding: "9px 16px", textDecoration: "none" }}
          >
            PDF
          </a>
          <a
            className="btn btn-ghost"
            href={`${base}/datasets/${datasetId}/export.pptx`}
            style={{ fontSize: 13, padding: "9px 16px", textDecoration: "none" }}
          >
            Slide deck
          </a>
          <button
            className="btn btn-ghost"
            onClick={() => (speaking ? stopSpeaking() : speak(current.summary))}
            disabled={!voiceAvailable}
            title={
              voiceAvailable
                ? "Read this finding aloud"
                : "This browser has no speech voices installed"
            }
            style={{ fontSize: 13, padding: "9px 16px" }}
          >
            {speaking ? "Stop" : "Read aloud"}
          </button>
        </div>
      </footer>
    </div>
  );
}

function RoundButton({
  children,
  onClick,
  disabled,
  primary,
  label,
}: {
  children: React.ReactNode;
  onClick: () => void;
  disabled?: boolean;
  primary?: boolean;
  label: string;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      style={{
        width: 40,
        height: 40,
        borderRadius: "50%",
        border: primary ? "none" : "1.5px solid var(--line-strong)",
        background: primary ? "var(--accent)" : "#fff",
        color: primary ? "#fff" : "var(--ink)",
        fontSize: 18,
        opacity: disabled ? 0.4 : 1,
        cursor: disabled ? "not-allowed" : "pointer",
        boxShadow: primary && !disabled ? "0 8px 18px rgba(232,90,50,0.3)" : "none",
      }}
    >
      {children}
    </button>
  );
}
