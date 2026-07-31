import Link from "next/link";
import { Wordmark } from "@/components/Brand";

const features = [
  {
    title: "Finds what you can't see",
    body: "Hidden margin leaks, quiet declines, products that sell but don't earn. Ranked by what matters most.",
  },
  {
    title: "Tells you what's real",
    body: "Every change is tested against your own normal variation, so a wobble is never dressed up as a trend.",
  },
  {
    title: "Leaves the call to you",
    body: "BusyLab shows you what's true and stops there. What to do about it stays your decision.",
  },
];

export default function Landing() {
  return (
    <main className="shell">
      <div className="frame">
        <nav
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "20px 40px",
          }}
        >
          <Wordmark />
          <Link href="/upload" className="btn" style={{ padding: "11px 22px", fontSize: 14.5 }}>
            Try it free
          </Link>
        </nav>

        <section
          style={{
            display: "flex",
            alignItems: "center",
            gap: 48,
            padding: "54px 64px 60px",
            flexWrap: "wrap",
          }}
        >
          <div style={{ flex: "1 1 420px", display: "flex", flexDirection: "column", gap: 20 }}>
            <div
              style={{
                alignSelf: "flex-start",
                padding: "7px 14px",
                borderRadius: "var(--radius-pill)",
                background: "var(--accent-wash)",
                color: "#9a3d1f",
                fontFamily: "var(--font-display)",
                fontWeight: 600,
                fontSize: 12.5,
                letterSpacing: "0.06em",
                textTransform: "uppercase",
              }}
            >
              Your business analyst, in a box
            </div>
            <h1 style={{ font: "800 42px/1.15 var(--font-display)" }}>
              Your spreadsheet already knows what&apos;s wrong. We make it talk.
            </h1>
            <p
              style={{
                margin: 0,
                fontSize: 17.5,
                lineHeight: 1.55,
                color: "var(--ink-muted)",
                maxWidth: 460,
              }}
            >
              BusyLab reads your sales file and hands back a guided story: what
              changed, whether it&apos;s real, and where it came from. No formulas.
              No dashboard to build.
            </p>
            <div style={{ display: "flex", gap: 14, marginTop: 6, flexWrap: "wrap" }}>
              <Link href="/upload" className="btn" style={{ fontSize: 16, padding: "15px 30px" }}>
                Upload your spreadsheet
              </Link>
            </div>
            <div style={{ fontSize: 13, color: "var(--ink-light)" }}>
              Works with Excel, CSV and Google Sheets exports · messy sheets welcome
            </div>
          </div>

          <div style={{ flex: "1 1 320px" }}>
            <div
              className="card"
              style={{ padding: "20px 22px", boxShadow: "var(--shadow-lift)" }}
            >
              <div
                style={{
                  fontFamily: "var(--font-display)",
                  fontWeight: 600,
                  fontSize: 11.5,
                  letterSpacing: "0.1em",
                  textTransform: "uppercase",
                  color: "#b06a1e",
                  marginBottom: 8,
                }}
              >
                Finding 1 · needs attention
              </div>
              <div style={{ font: "600 17px/1.4 var(--font-display)" }}>
                Revenue is down 18% since March, and it&apos;s not seasonal.
              </div>
              <svg viewBox="0 0 380 120" style={{ width: "100%", marginTop: 12 }}>
                <rect x="190" y="10" width="180" height="90" rx="6" fill="#fbede7" />
                <text
                  x="280"
                  y="26"
                  textAnchor="middle"
                  fontFamily="Albert Sans"
                  fontSize="10"
                  fontWeight="600"
                  fill="#c74722"
                >
                  real decline
                </text>
                <path
                  d="M10,45 L70,42 L130,50 L190,48 L250,66 L310,84 L370,95"
                  fill="none"
                  stroke="#e85a32"
                  strokeWidth="2.5"
                  strokeLinecap="round"
                />
                <path d="M10,48 L370,44" stroke="#d8d2c7" strokeWidth="1.5" strokeDasharray="4 5" />
                <circle cx="370" cy="95" r="4" fill="#e85a32" />
              </svg>
            </div>
          </div>
        </section>

        <section
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
            gap: 18,
            padding: "0 64px 56px",
          }}
        >
          {features.map((f) => (
            <div key={f.title} className="card" style={{ padding: 24 }}>
              <div style={{ font: "700 16.5px var(--font-display)", marginBottom: 10 }}>
                {f.title}
              </div>
              <div style={{ fontSize: 14.5, lineHeight: 1.55, color: "var(--ink-muted)" }}>
                {f.body}
              </div>
            </div>
          ))}
        </section>

        <section
          style={{
            margin: "0 40px 40px",
            borderRadius: 20,
            background: "var(--ink)",
            padding: "34px 40px",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 24,
            flexWrap: "wrap",
          }}
        >
          <div>
            <div style={{ font: "700 22px var(--font-display)", color: "var(--card)" }}>
              Five minutes from spreadsheet to story.
            </div>
            <div style={{ fontSize: 14.5, color: "var(--ink-faint)", marginTop: 6 }}>
              Everything free while BusyLab is in beta.
            </div>
          </div>
          <Link href="/upload" className="btn" style={{ fontSize: 15.5 }}>
            Get started
          </Link>
        </section>
      </div>
    </main>
  );
}
