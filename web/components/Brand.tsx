import Link from "next/link";

export function Logo({ size = 28 }: { size?: number }) {
  return (
    <div
      style={{
        width: size,
        height: size,
        borderRadius: size * 0.32,
        background: "var(--accent)",
        display: "grid",
        placeItems: "center",
        color: "#fff",
        fontFamily: "var(--font-display)",
        fontWeight: 800,
        fontSize: size * 0.54,
      }}
    >
      B
    </div>
  );
}

export function Wordmark({ size = 18 }: { size?: number }) {
  return (
    <Link
      href="/"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        color: "var(--ink)",
      }}
    >
      <Logo size={size * 1.5} />
      <span
        style={{
          fontFamily: "var(--font-display)",
          fontWeight: 700,
          fontSize: size,
          letterSpacing: "0.02em",
        }}
      >
        BusyLab
      </span>
    </Link>
  );
}

/** The three-step progress rail shown while a file is being understood. */
export function Stepper({ active }: { active: 0 | 1 | 2 }) {
  const steps = ["Upload", "Check columns", "Your insights"];
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      {steps.map((label, i) => {
        const done = i < active;
        const current = i === active;
        return (
          <div key={label} style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <div
              style={{
                width: 22,
                height: 22,
                borderRadius: "50%",
                display: "grid",
                placeItems: "center",
                fontFamily: "var(--font-display)",
                fontWeight: 700,
                fontSize: 11,
                color: done ? "#fff" : current ? "var(--accent)" : "var(--ink-faint)",
                background: done ? "var(--good)" : "#fff",
                border: `1.5px solid ${
                  done ? "var(--good)" : current ? "var(--accent)" : "#e5dfd5"
                }`,
              }}
            >
              {i + 1}
            </div>
            <span
              style={{
                fontSize: 13.5,
                color: current ? "var(--ink)" : "var(--ink-light)",
                fontWeight: i <= active ? 600 : 400,
              }}
            >
              {label}
            </span>
            {i < 2 && <span style={{ color: "#d8d2c7", margin: "0 2px" }}>›</span>}
          </div>
        );
      })}
    </div>
  );
}

export function AppHeader({ children }: { children?: React.ReactNode }) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "16px 32px",
        borderBottom: "1px solid #efece6",
        background: "var(--card)",
        gap: 20,
        flexWrap: "wrap",
      }}
    >
      <Wordmark size={16} />
      {children}
    </div>
  );
}
