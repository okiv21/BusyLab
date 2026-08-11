"use client";

/**
 * What BusyLab noticed without being asked (spec Pillar 2).
 *
 * This is the retention surface - the thing that makes BusyLab a system that
 * works for you rather than a tool you remember to open. It stays quiet by
 * design: an empty feed is the normal state for a business behaving normally,
 * and saying so plainly is more reassuring than padding it out.
 */

import { useCallback, useEffect, useState } from "react";
import { motion } from "framer-motion";
import { acknowledgeAlert, getDigest, listAlerts, sendDigest } from "@/lib/api";
import type { Alert, DigestDelivery,
  DigestPreview } from "@/lib/types";

const LEVEL: Record<string, { label: string; fg: string; bg: string }> = {
  high: { label: "HIGH", fg: "#c74722", bg: "#fbe3d8" },
  medium: { label: "MEDIUM", fg: "#b06a1e", bg: "#fdf6ec" },
  good: { label: "GOOD", fg: "#177e5b", bg: "#e7f6f0" },
  info: { label: "INFO", fg: "#6e675c", bg: "#f0ede7" },
};

export default function AlertsPanel({ datasetId }: { datasetId: string }) {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [digest, setDigest] = useState<DigestPreview | null>(null);
  const [showDigest, setShowDigest] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [a, d] = await Promise.all([
        listAlerts(datasetId),
        getDigest(datasetId).catch(() => null),
      ]);
      setAlerts(a.alerts);
      setDigest(d);
    } catch {
      /* the feed is additive; a failure here must not break the story */
    } finally {
      setLoaded(true);
    }
  }, [datasetId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const dismiss = async (key: string) => {
    setAlerts((current) => current.filter((a) => a.key !== key));
    try {
      await acknowledgeAlert(datasetId, key);
    } catch {
      refresh();
    }
  };

  if (!loaded) return null;

  return (
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
          <div style={{ font: "600 15px var(--font-display)" }}>
            We&apos;re watching so you don&apos;t have to
          </div>
          <div style={{ fontSize: 13.5, color: "var(--ink-muted)", marginTop: 3 }}>
            {alerts.length === 0
              ? "Nothing has broken pattern. That is the normal state."
              : `${alerts.length} thing${alerts.length === 1 ? "" : "s"} broke pattern since the last check.`}
          </div>
        </div>
        {digest && !digest.is_empty && (
          <button
            className="btn btn-ghost"
            onClick={() => setShowDigest((v) => !v)}
            style={{ fontSize: 13.5, padding: "10px 18px" }}
          >
            {showDigest ? "Hide the email" : "Preview the email"}
          </button>
        )}
      </div>

      {alerts.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {alerts.map((alert) => {
            const tone = LEVEL[alert.level] ?? LEVEL.info;
            return (
              <motion.div
                key={alert.key}
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                style={{
                  background: "#fff",
                  border: "1px solid var(--line)",
                  borderRadius: 16,
                  padding: "14px 18px",
                  display: "flex",
                  gap: 14,
                  alignItems: "flex-start",
                }}
              >
                <span
                  className="pill"
                  style={{
                    flex: "0 0 auto",
                    marginTop: 2,
                    fontFamily: "var(--font-display)",
                    fontWeight: 700,
                    fontSize: 10.5,
                    letterSpacing: "0.06em",
                    color: tone.fg,
                    background: tone.bg,
                  }}
                >
                  {tone.label}
                </span>
                <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 4 }}>
                  <div style={{ fontSize: 15, fontWeight: 600, lineHeight: 1.4 }}>
                    {alert.title}
                  </div>
                  <div style={{ fontSize: 13.5, color: "var(--ink-muted)", lineHeight: 1.5 }}>
                    {alert.detail}
                  </div>
                  {alert.period && alert.period !== "current" && (
                    <div style={{ fontSize: 12, color: "var(--ink-light)" }}>
                      {alert.subject} · {alert.period}
                    </div>
                  )}
                </div>
                <button
                  onClick={() => dismiss(alert.key)}
                  aria-label="Dismiss this alert"
                  style={{
                    flex: "0 0 auto",
                    border: "none",
                    background: "transparent",
                    color: "var(--ink-light)",
                    fontSize: 13,
                  }}
                >
                  Dismiss
                </button>
              </motion.div>
            );
          })}
        </div>
      )}

      {showDigest && digest && (
        <motion.div
          initial={{ opacity: 0, height: 0 }}
          animate={{ opacity: 1, height: "auto" }}
          style={{
            overflow: "hidden",
            border: "1px solid var(--line)",
            borderRadius: 16,
            background: "#fff",
          }}
        >
          <div
            style={{
              padding: "12px 18px",
              borderBottom: "1px solid var(--line)",
              fontFamily: "var(--font-display)",
              fontWeight: 600,
              fontSize: 12.5,
              color: "var(--ink-light)",
              letterSpacing: "0.08em",
              textTransform: "uppercase",
            }}
          >
            Your Monday email
          </div>
          {/* The engine renders the email; this shows exactly what would be
              sent rather than a second implementation of it. */}
          <div dangerouslySetInnerHTML={{ __html: digest.html }} />

          {/* Who receives it, when, and a way to prove it works.
              Without this the preview was a picture of an email with nothing
              to say whether anything would ever send it - which is why it read
              as decoration when the delivery behind it was real. */}
          <DigestDeliveryBar
            datasetId={datasetId}
            delivery={digest.delivery}
          />
        </motion.div>
      )}
    </div>
  );
}


/**
 * Where this email goes and when, with a way to send it now.
 *
 * A rendered preview says nothing about whether delivery works, and the only
 * other way to find out was to wait for Monday. Sending on demand answers it
 * in one click, and the result is reported honestly: with no mail server
 * configured the digest goes to the server log, and that is reported as not
 * sent rather than as success.
 */
function DigestDeliveryBar({
  datasetId,
  delivery,
}: {
  datasetId: string;
  delivery?: DigestDelivery;
}) {
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  if (!delivery) return null;

  const send = async () => {
    setSending(true);
    setResult(null);
    try {
      const outcome = await sendDigest(datasetId);
      setResult(outcome.detail);
    } catch (err) {
      setResult(err instanceof Error ? err.message : "Could not send it.");
    } finally {
      setSending(false);
    }
  };

  return (
    <div
      style={{
        borderTop: "1px solid var(--line)",
        padding: "12px 18px",
        display: "flex",
        alignItems: "center",
        gap: 12,
        flexWrap: "wrap",
        fontSize: 13,
        color: "var(--ink-muted)",
      }}
    >
      <div style={{ flex: "1 1 260px", lineHeight: 1.55 }}>
        {delivery.recipient ? (
          <>
            Goes to <strong>{delivery.recipient}</strong>
            {delivery.is_fallback && " (the fallback address)"} · {delivery.schedule}
          </>
        ) : (
          <>
            No address is set for this data yet, so nothing is being sent.
          </>
        )}
        {!delivery.can_send && (
          <div style={{ marginTop: 4, color: "var(--ink-light)" }}>
            No mail server is configured, so it is written to the server log
            rather than emailed.
          </div>
        )}
      </div>

      {delivery.recipient && (
        <button
          onClick={send}
          disabled={sending}
          className="btn btn-ghost"
          style={{ fontSize: 13, padding: "8px 14px" }}
        >
          {sending ? "Sending…" : "Send it to me now"}
        </button>
      )}

      {result && (
        <div style={{ flexBasis: "100%", fontSize: 12.5, color: "var(--ink-light)" }}>
          {result}
        </div>
      )}
    </div>
  );
}
