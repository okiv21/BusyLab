"use client";

import { useCallback, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { motion } from "framer-motion";
import { AppHeader, Stepper } from "@/components/Brand";
import { uploadFile, waitForJob } from "@/lib/api";

export default function UploadPage() {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [step, setStep] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");

  const send = useCallback(
    async (file: File) => {
      setError(null);
      setBusy(true);
      setName(file.name);
      try {
        const { job_id, dataset_id } = await uploadFile(file);
        // Detection runs off the request cycle, so we poll rather than block.
        const job = await waitForJob(job_id, (j) =>
          setStep(j.step || "reading the file")
        );
        if (job.status === "failed") {
          setError(job.error ?? "That file could not be read.");
          setBusy(false);
          return;
        }
        router.push(`/d/${dataset_id}`);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Something went wrong.");
        setBusy(false);
      }
    },
    [router]
  );

  return (
    <main className="shell">
      <div className="frame">
        <AppHeader>
          <Stepper active={0} />
          <span />
        </AppHeader>

        <motion.section
          initial={{ opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
          style={{
            padding: "var(--pad-section)",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 32,
            textAlign: "center",
          }}
        >
          <div style={{ maxWidth: 540 }}>
            <h1 style={{ font: "700 32px/1.2 var(--font-display)", marginBottom: 12 }}>
              Let&apos;s meet your business.
            </h1>
            <p style={{ margin: 0, fontSize: 16.5, lineHeight: 1.5, color: "var(--ink-muted)" }}>
              Drop in your sales spreadsheet and BusyLab will turn it into a
              plain-English story of what&apos;s working and what needs a look.
            </p>
          </div>

          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              const file = e.dataTransfer.files?.[0];
              if (file && !busy) send(file);
            }}
            onClick={() => !busy && inputRef.current?.click()}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if ((e.key === "Enter" || e.key === " ") && !busy) {
                inputRef.current?.click();
              }
            }}
            style={{
              cursor: busy ? "default" : "pointer",
              width: "100%",
              maxWidth: 620,
              border: `2px dashed ${dragging ? "var(--accent)" : "#dcc9bf"}`,
              background: "linear-gradient(180deg, #fdf9f5, #fbf4ee)",
              borderRadius: 22,
              padding: "46px 40px",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: 16,
              transition: "border-color 0.25s",
            }}
          >
            <input
              ref={inputRef}
              type="file"
              accept=".xlsx,.xlsm,.xls,.csv,.tsv"
              style={{ display: "none" }}
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) send(file);
              }}
            />

            {busy ? (
              <>
                <Spinner />
                <div style={{ font: "600 16px var(--font-display)" }}>{name}</div>
                <div style={{ fontSize: 14, color: "var(--ink-light)" }}>
                  {step || "reading the file"}…
                </div>
              </>
            ) : (
              <>
                <div
                  style={{
                    width: 62,
                    height: 62,
                    borderRadius: 19,
                    background: "#fff",
                    boxShadow: "0 8px 20px rgba(232,90,50,0.16)",
                    display: "grid",
                    placeItems: "center",
                  }}
                >
                  <svg width="27" height="27" viewBox="0 0 28 28">
                    <path
                      d="M14 20V6M14 6l-6 6M14 6l6 6"
                      stroke="#e85a32"
                      strokeWidth="2.5"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      fill="none"
                    />
                  </svg>
                </div>
                <div>
                  <div style={{ font: "600 17px var(--font-display)" }}>
                    Drag your file here
                  </div>
                  <div style={{ fontSize: 14, color: "var(--ink-light)", marginTop: 5 }}>
                    Excel or CSV
                  </div>
                </div>
                <span className="btn">Choose a file</span>
              </>
            )}
          </div>

          {error && (
            <div
              role="alert"
              style={{
                maxWidth: 620,
                background: "#fff3ec",
                border: "1.5px solid #f3d9cc",
                borderRadius: 14,
                padding: "14px 18px",
                fontSize: 14.5,
                color: "#c74722",
                textAlign: "left",
              }}
            >
              {error}
            </div>
          )}

          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              color: "var(--ink-light)",
              fontSize: 13.5,
            }}
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#1fa97a" strokeWidth="2.2" strokeLinecap="round">
              <path d="M20 6 9 17l-5-5" />
            </svg>
            Messy sheets welcome: merged headers, total rows, half-empty columns.
          </div>
        </motion.section>
      </div>
    </main>
  );
}

function Spinner() {
  return (
    <div
      style={{
        width: 40,
        height: 40,
        borderRadius: "50%",
        border: "3px solid #f0e7df",
        borderTopColor: "var(--accent)",
        animation: "spin 0.9s linear infinite",
      }}
    >
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}
