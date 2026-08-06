"use client";

/**
 * One chart per finding, chosen by the engine rather than by the user.
 *
 * Spec 7: chart selection is a deterministic mapping from finding type, and
 * the finding carries the answer. This component therefore switches on
 * `finding.chart` and never decides anything itself. There is no chart picker
 * anywhere in this app on purpose (spec 6, the Tableau trap).
 *
 * Deliberately absent: 3D, radar and gauges. They distort perception.
 */

import {
  Area,
  Bar,
  BarChart,
  Cell,
  ComposedChart,
  Line,
  Pie,
  PieChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  Treemap,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";
import { useState } from "react";
import type { Finding } from "@/lib/types";
import { compact, money, percent } from "@/lib/format";
import { CHART_MS } from "@/lib/motion";

/**
 * Hover state for the "highlight the point, dim the rest" micro-interaction
 * (spec 7). Returns the opacity a given index should take: full when nothing
 * is hovered or when this is the hovered one, faded otherwise.
 *
 * Dimming the others rather than brightening the hovered one is deliberate.
 * Raising one element makes the chart appear to flicker as the pointer
 * travels; lowering the rest keeps the overall weight of the picture steady.
 */
function useHoverFocus() {
  const [active, setActive] = useState<number | null>(null);
  const opacityFor = (index: number) =>
    active === null || active === index ? 1 : 0.32;

  /**
   * Props for a plain wrapper around the chart, which is what actually clears
   * the focus. Recharts' own `onMouseLeave` does not fire reliably when the
   * pointer exits the chart area, and without a reset the chart stays dimmed
   * forever after one hover — worse than having no interaction at all.
   */
  const reset = {
    onMouseLeave: () => setActive(null),
    onBlur: () => setActive(null),
  };

  return { active, setActive, opacityFor, reset };
}

const ACCENT = "#e85a32";
const ACCENT_SOFT = "#f6d5c8";
const GOOD = "#1fa97a";
const GREY = "#b8b0a2";
const LINE = "#efeae2";
const INK_LIGHT = "#8a8378";

/** Ordered tints for categorical series. Sorted data reads darkest first. */
const RAMP = ["#e85a32", "#ee8563", "#f3bba6", "#f6d5c8", "#f9e6de", "#efe3dc"];

const axis = {
  stroke: LINE,
  tick: { fill: INK_LIGHT, fontSize: 11.5, fontFamily: "Albert Sans" },
  tickLine: false,
};

const tooltipStyle = {
  contentStyle: {
    borderRadius: 12,
    border: "1px solid #f0ebe3",
    boxShadow: "0 8px 24px rgba(33,28,21,0.10)",
    fontSize: 13,
    fontFamily: "Albert Sans",
  },
} as const;

function Empty({ label }: { label: string }) {
  return (
    <div
      style={{
        padding: "28px 0",
        color: INK_LIGHT,
        fontSize: 13.5,
        textAlign: "center",
      }}
    >
      {label}
    </div>
  );
}

/* --- change over time: is this real, or wobble? ----------------------- */

function TrendChart({ finding }: { finding: Finding }) {
  const series = (finding.chart_data?.series ?? []) as {
    period: string;
    value: number;
  }[];
  if (series.length < 2) return <Empty label="Not enough history to draw yet." />;

  // Straight line through the data, so "is this a trend" is visible and not
  // just asserted in the sentence.
  const n = series.length;
  const meanX = (n - 1) / 2;
  const meanY = series.reduce((s, d) => s + d.value, 0) / n;
  let num = 0;
  let den = 0;
  series.forEach((d, i) => {
    num += (i - meanX) * (d.value - meanY);
    den += (i - meanX) ** 2;
  });
  const slope = den === 0 ? 0 : num / den;
  const real = finding.type === "trend";

  const data = series.map((d, i) => ({
    ...d,
    fit: meanY + slope * (i - meanX),
  }));

  return (
    <ResponsiveContainer width="100%" height={230}>
      <ComposedChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
        <XAxis dataKey="period" {...axis} minTickGap={40} />
        <YAxis {...axis} width={62} tickFormatter={(v) => compact(v)} />
        <Tooltip
          {...tooltipStyle}
          formatter={(v: number, name) => [
            money(v),
            name === "fit" ? "trend" : "revenue",
          ]}
        />
        <Line
          type="monotone"
          dataKey="value"
          stroke={ACCENT}
          strokeWidth={2.5}
          dot={false}
          activeDot={{ r: 5 }}
          isAnimationActive
          animationDuration={520}
        />
        <Line
          type="linear"
          dataKey="fit"
          stroke={real ? ACCENT : GREY}
          strokeWidth={1.5}
          strokeDasharray="5 6"
          dot={false}
          isAnimationActive={false}
        />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

/* --- compare products: who is biggest, in order ----------------------- */

function RankingChart({ finding }: { finding: Finding }) {
  const bars = (finding.chart_data?.bars ?? []) as {
    label: string;
    value: number;
  }[];
  const { setActive, opacityFor, reset } = useHoverFocus();
  if (!bars.length) return <Empty label="No products to compare." />;

  return (
    <div {...reset}>
    <ResponsiveContainer width="100%" height={Math.max(150, bars.length * 38)}>
      <BarChart
        data={bars}
        layout="vertical"
        margin={{ top: 4, right: 56, bottom: 4, left: 8 }}
      >
        <XAxis type="number" hide />
        <YAxis
          type="category"
          dataKey="label"
          {...axis}
          width={128}
          tick={{ fill: "#211c15", fontSize: 13, fontFamily: "Albert Sans" }}
        />
        <Tooltip
          {...tooltipStyle}
          cursor={{ fill: "rgba(232,90,50,0.05)" }}
          formatter={(v: number) => [money(v), "revenue"]}
        />
        <Bar
          dataKey="value"
          radius={[6, 6, 6, 6]}
          animationDuration={CHART_MS}
          onMouseEnter={(_: unknown, i: number) => setActive(i)}
        >
          {bars.map((_, i) => (
            <Cell
              key={i}
              fill={RAMP[Math.min(i, RAMP.length - 1)]}
              fillOpacity={opacityFor(i)}
              style={{ transition: "fill-opacity 0.2s" }}
            />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
    </div>
  );
}

/* --- composition: donut for a few, treemap for many ------------------- */

function ConcentrationChart({ finding }: { finding: Finding }) {
  const slices = (finding.chart_data?.slices ?? []) as {
    label: string;
    value: number;
    share: number;
  }[];
  const { active, setActive, opacityFor, reset } = useHoverFocus();
  if (!slices.length) return <Empty label="Nothing to break down." />;

  if (finding.chart === "treemap") {
    const data = slices.map((s, i) => ({
      name: s.label,
      size: s.value,
      fill: RAMP[Math.min(i, RAMP.length - 1)],
    }));
    return (
      <ResponsiveContainer width="100%" height={240}>
        <Treemap
          data={data}
          dataKey="size"
          stroke="#fff"
          animationDuration={520}
          content={<TreemapCell />}
        />
      </ResponsiveContainer>
    );
  }

  const leader = slices[0];
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 34, flexWrap: "wrap" }}>
      <div
        {...reset}
        style={{ position: "relative", width: 190, height: 190, flex: "0 0 auto" }}
      >
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={slices}
              dataKey="value"
              nameKey="label"
              innerRadius={58}
              outerRadius={88}
              paddingAngle={1.5}
              startAngle={90}
              endAngle={-270}
              animationDuration={620}
              stroke="none"
              onMouseEnter={(_: unknown, i: number) => setActive(i)}
              onMouseLeave={() => setActive(null)}
            >
              {slices.map((_, i) => (
                <Cell
                  key={i}
                  fill={RAMP[Math.min(i, RAMP.length - 1)]}
                  fillOpacity={opacityFor(i)}
                  style={{ transition: "fill-opacity 0.2s" }}
                />
              ))}
            </Pie>
            <Tooltip {...tooltipStyle} formatter={(v: number) => money(v)} />
          </PieChart>
        </ResponsiveContainer>
        <div
          style={{
            position: "absolute",
            inset: 0,
            display: "grid",
            placeItems: "center",
            pointerEvents: "none",
          }}
        >
          {/* The centre follows the pointer, so hovering answers a question
              rather than just lighting something up. */}
          <div style={{ textAlign: "center" }}>
            <div
              style={{
                font: "800 30px Sora",
                color: "#211c15",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {percent((active !== null ? slices[active] : leader).share)}
            </div>
            <div
              style={{
                fontSize: 11,
                color: INK_LIGHT,
                maxWidth: 112,
                margin: "0 auto",
              }}
            >
              {active !== null
                ? slices[active].label
                : `of ${finding.facts?.metric ?? "total"}`}
            </div>
          </div>
        </div>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 9, fontSize: 14.5 }}>
        {slices.slice(0, 5).map((s, i) => (
          <div key={s.label} style={{ display: "flex", alignItems: "center", gap: 9 }}>
            <span
              style={{
                width: 11,
                height: 11,
                borderRadius: 4,
                background: RAMP[Math.min(i, RAMP.length - 1)],
              }}
            />
            {s.label} · {money(s.value)}
          </div>
        ))}
      </div>
    </div>
  );
}

function TreemapCell(props: any) {
  const { x, y, width, height, name, fill } = props;
  if (width < 2 || height < 2) return null;
  return (
    <g>
      <rect x={x} y={y} width={width} height={height} fill={fill} rx={6} stroke="#fff" />
      {width > 68 && height > 30 && (
        <text
          x={x + 10}
          y={y + 22}
          fontFamily="Albert Sans"
          fontSize={12.5}
          fontWeight={600}
          fill={fill === RAMP[0] || fill === RAMP[1] ? "#fff" : "#211c15"}
        >
          {name}
        </text>
      )}
    </g>
  );
}

/* --- two metrics in tension: the low-margin best seller ---------------- */

function TensionChart({ finding }: { finding: Finding }) {
  const points = (finding.chart_data?.points ?? []) as {
    product: string;
    revenue: number;
    profit: number;
    margin: number;
  }[];
  if (!points.length) return <Empty label="Cost data is needed for this view." />;

  const highlight = finding.facts?.top_seller ?? finding.facts?.product;
  const data = points.map((p) => ({
    ...p,
    marginPct: Number.isFinite(p.margin) ? p.margin * 100 : 0,
  }));

  return (
    <ResponsiveContainer width="100%" height={260}>
      <ScatterChart margin={{ top: 16, right: 24, bottom: 26, left: 8 }}>
        <XAxis
          type="number"
          dataKey="revenue"
          name="revenue"
          {...axis}
          tickFormatter={(v) => compact(v)}
          label={{
            value: "revenue →",
            position: "insideBottom",
            offset: -14,
            fill: INK_LIGHT,
            fontSize: 11.5,
          }}
        />
        <YAxis
          type="number"
          dataKey="marginPct"
          name="margin"
          {...axis}
          width={54}
          tickFormatter={(v) => `${Math.round(v)}%`}
        />
        <ZAxis type="number" dataKey="profit" range={[80, 420]} />
        <ReferenceLine y={0} stroke={GREY} strokeDasharray="4 5" />
        <Tooltip
          {...tooltipStyle}
          cursor={{ strokeDasharray: "3 3" }}
          formatter={(v: number, name) =>
            name === "margin" ? [`${v.toFixed(0)}%`, "margin"] : [money(v), name]
          }
          labelFormatter={() => ""}
          content={({ payload }) => {
            const p = payload?.[0]?.payload;
            if (!p) return null;
            return (
              <div
                style={{
                  background: "#fff",
                  border: "1px solid #f0ebe3",
                  borderRadius: 12,
                  padding: "10px 12px",
                  fontSize: 13,
                  boxShadow: "0 8px 24px rgba(33,28,21,0.10)",
                }}
              >
                <strong>{p.product}</strong>
                <div style={{ color: INK_LIGHT }}>
                  {money(p.revenue)} revenue · {(p.marginPct).toFixed(0)}% margin
                </div>
              </div>
            );
          }}
        />
        <Scatter data={data} animationDuration={520}>
          {data.map((p, i) => (
            <Cell
              key={i}
              fill={
                p.product === highlight
                  ? ACCENT
                  : p.marginPct < 0
                    ? "#e5dfd5"
                    : GOOD
              }
              fillOpacity={p.product === highlight ? 1 : 0.85}
            />
          ))}
        </Scatter>
      </ScatterChart>
    </ResponsiveContainer>
  );
}

/* --- decomposition: why the number moved ------------------------------ */

function WaterfallChart({ finding }: { finding: Finding }) {
  const steps = (finding.chart_data?.steps ?? []) as {
    label: string;
    change: number;
  }[];
  const start = finding.chart_data?.start;
  const end = finding.chart_data?.end;
  if (!steps.length || !start) return <Empty label="Nothing to decompose." />;

  // Hand-rolled rather than Recharts: a waterfall is a running balance, which
  // means invisible offset bars, and the arithmetic is clearer written out.
  const shown = steps.filter((s) => s.change !== 0).slice(0, 7);
  let running = start.value;
  const bars = shown.map((s) => {
    const from = running;
    running += s.change;
    return { ...s, from, to: running };
  });

  const values = [start.value, end?.value ?? running, ...bars.flatMap((b) => [b.from, b.to])];
  const max = Math.max(...values, 0);
  const min = Math.min(...values, 0);
  const span = max - min || 1;

  const W = 720;
  const H = 210;
  const pad = { top: 26, bottom: 40 };
  const plot = H - pad.top - pad.bottom;
  const cols = bars.length + 2;
  const slot = W / cols;
  const barW = Math.min(74, slot * 0.6);
  const y = (v: number) => pad.top + plot - ((v - min) / span) * plot;

  const column = (i: number) => i * slot + slot / 2 - barW / 2;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto" }}>
      <line x1={0} y1={y(min)} x2={W} y2={y(min)} stroke={LINE} />
      {/* opening */}
      <rect
        x={column(0)}
        y={y(start.value)}
        width={barW}
        height={Math.max(2, y(min) - y(start.value))}
        rx={6}
        fill={GREY}
      />
      <text
        x={column(0) + barW / 2}
        y={y(start.value) - 8}
        textAnchor="middle"
        fontFamily="Albert Sans"
        fontSize={11.5}
        fontWeight={600}
        fill={INK_LIGHT}
      >
        {compact(start.value)}
      </text>
      <text
        x={column(0) + barW / 2}
        y={H - 14}
        textAnchor="middle"
        fontFamily="Albert Sans"
        fontSize={11}
        fill={INK_LIGHT}
      >
        {start.label}
      </text>

      {bars.map((b, i) => {
        const top = y(Math.max(b.from, b.to));
        const height = Math.max(2, Math.abs(y(b.from) - y(b.to)));
        const positive = b.change > 0;
        return (
          <g key={b.label}>
            <rect
              x={column(i + 1)}
              y={top}
              width={barW}
              height={height}
              rx={5}
              fill={positive ? GOOD : ACCENT}
              opacity={0.92}
            >
              <animate
                attributeName="height"
                from="0"
                to={String(height)}
                dur="0.5s"
                fill="freeze"
              />
            </rect>
            <text
              x={column(i + 1) + barW / 2}
              y={top - 8}
              textAnchor="middle"
              fontFamily="Albert Sans"
              fontSize={11.5}
              fontWeight={600}
              fill={positive ? "#177e5b" : "#c74722"}
            >
              {positive ? "+" : "−"}
              {compact(Math.abs(b.change))}
            </text>
            <text
              x={column(i + 1) + barW / 2}
              y={H - 14}
              textAnchor="middle"
              fontFamily="Albert Sans"
              fontSize={11}
              fill={INK_LIGHT}
            >
              {b.label.length > 13 ? `${b.label.slice(0, 12)}…` : b.label}
            </text>
          </g>
        );
      })}

      {end && (
        <>
          <rect
            x={column(cols - 1)}
            y={y(end.value)}
            width={barW}
            height={Math.max(2, y(min) - y(end.value))}
            rx={6}
            fill={GREY}
            opacity={0.55}
          />
          <text
            x={column(cols - 1) + barW / 2}
            y={y(end.value) - 8}
            textAnchor="middle"
            fontFamily="Albert Sans"
            fontSize={11.5}
            fontWeight={600}
            fill={INK_LIGHT}
          >
            {compact(end.value)}
          </text>
          <text
            x={column(cols - 1) + barW / 2}
            y={H - 14}
            textAnchor="middle"
            fontFamily="Albert Sans"
            fontSize={11}
            fill={INK_LIGHT}
          >
            {end.label}
          </text>
        </>
      )}
    </svg>
  );
}

/* --- segmentation ------------------------------------------------------ */

function SegmentChart({ finding }: { finding: Finding }) {
  const groups = (finding.chart_data?.groups ?? []) as {
    label: string;
    value: number;
  }[];
  const { setActive, opacityFor, reset } = useHoverFocus();
  if (!groups.length) return <Empty label="No groups to compare." />;

  return (
    <div {...reset}>
      <ResponsiveContainer width="100%" height={Math.max(160, groups.length * 44)}>
        <BarChart
          data={groups}
          layout="vertical"
          margin={{ top: 4, right: 56, bottom: 4, left: 8 }}
        >
          <XAxis type="number" hide />
          <YAxis
            type="category"
            dataKey="label"
            {...axis}
            width={120}
            tick={{ fill: "#211c15", fontSize: 13, fontFamily: "Albert Sans" }}
          />
          <Tooltip
            {...tooltipStyle}
            cursor={{ fill: "rgba(232,90,50,0.05)" }}
            formatter={(v: number) => [money(v), "average"]}
          />
          <Bar
            dataKey="value"
            radius={[6, 6, 6, 6]}
            animationDuration={CHART_MS}
            onMouseEnter={(_: unknown, i: number) => setActive(i)}
          >
            {groups.map((_, i) => (
              <Cell
                key={i}
                fill={RAMP[Math.min(i, RAMP.length - 1)]}
                fillOpacity={opacityFor(i)}
                style={{ transition: "fill-opacity 0.2s" }}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/* --- what moves together ---------------------------------------------- */

function CorrelationChart({ finding }: { finding: Finding }) {
  const products = (finding.chart_data?.products ?? []) as string[];
  const matrix = (finding.chart_data?.matrix ?? []) as {
    a: string;
    b: string;
    correlation: number;
  }[];
  if (products.length < 2) return <Empty label="Not enough products to compare." />;

  const lookup = new Map<string, number>();
  matrix.forEach((m) => {
    lookup.set(`${m.a}|${m.b}`, m.correlation);
    lookup.set(`${m.b}|${m.a}`, m.correlation);
  });

  const cell = 40;
  const labelW = 118;
  const size = products.length * cell;

  const colour = (r: number) => {
    if (r >= 0) return `rgba(232, 90, 50, ${Math.min(0.12 + r * 0.85, 0.95)})`;
    return `rgba(31, 169, 122, ${Math.min(0.12 + Math.abs(r) * 0.85, 0.95)})`;
  };

  return (
    <div style={{ overflowX: "auto" }}>
      <svg
        viewBox={`0 0 ${labelW + size + 8} ${size + 96}`}
        style={{ width: "100%", minWidth: 420, height: "auto" }}
      >
        {products.map((p, row) =>
          products.map((q, col) => {
            const r = row === col ? 1 : lookup.get(`${p}|${q}`);
            if (r === undefined) return null;
            return (
              <rect
                key={`${row}-${col}`}
                x={labelW + col * cell}
                y={row * cell}
                width={cell - 3}
                height={cell - 3}
                rx={5}
                fill={row === col ? "#f0ebe3" : colour(r)}
              />
            );
          })
        )}
        {products.map((p, row) => (
          <text
            key={p}
            x={labelW - 10}
            y={row * cell + cell / 2 - 1}
            textAnchor="end"
            dominantBaseline="middle"
            fontFamily="Albert Sans"
            fontSize={12}
            fill="#211c15"
          >
            {p.length > 16 ? `${p.slice(0, 15)}…` : p}
          </text>
        ))}
        {products.map((p, col) => (
          <text
            key={`c-${p}`}
            x={labelW + col * cell + cell / 2 - 2}
            y={size + 12}
            fontFamily="Albert Sans"
            fontSize={11}
            fill={INK_LIGHT}
            transform={`rotate(38 ${labelW + col * cell + cell / 2} ${size + 12})`}
          >
            {p.length > 14 ? `${p.slice(0, 13)}…` : p}
          </text>
        ))}
      </svg>
    </div>
  );
}

/* --- forecast: where this is heading, and how sure we are -------------- */

function ForecastChart({ finding }: { finding: Finding }) {
  const history = (finding.chart_data?.history ?? []) as {
    period: string;
    value: number;
  }[];
  const forecast = (finding.chart_data?.forecast ?? []) as {
    period: string;
    mean: number;
    lower80: number;
    upper80: number;
    lower95: number;
    upper95: number;
  }[];
  if (!history.length || !forecast.length) {
    return <Empty label="Not enough history to project from." />;
  }

  // One continuous series so the fan starts exactly where the history stops.
  const last = history[history.length - 1];
  const data = [
    ...history.map((h) => ({
      period: h.period,
      actual: h.value,
      mean: null as number | null,
      band80: null as [number, number] | null,
      band95: null as [number, number] | null,
    })),
    // Join point: the fan is pinned to the final actual value.
    {
      period: last.period,
      actual: last.value,
      mean: last.value,
      band80: [last.value, last.value] as [number, number],
      band95: [last.value, last.value] as [number, number],
    },
    ...forecast.map((f) => ({
      period: f.period,
      actual: null as number | null,
      mean: f.mean,
      band80: [f.lower80, f.upper80] as [number, number],
      band95: [f.lower95, f.upper95] as [number, number],
    })),
  ];

  const crossesBreakEven = finding.facts?.crosses_break_even === true;

  return (
    <ResponsiveContainer width="100%" height={250}>
      <ComposedChart data={data} margin={{ top: 10, right: 14, bottom: 4, left: 4 }}>
        <XAxis dataKey="period" {...axis} minTickGap={44} />
        <YAxis {...axis} width={64} tickFormatter={(v) => compact(v)} />
        <Tooltip
          {...tooltipStyle}
          formatter={(v: any, name) => {
            if (Array.isArray(v)) return [`${compact(v[0])} – ${compact(v[1])}`, name];
            return [money(v as number), name === "actual" ? "actual" : "projected"];
          }}
        />

        {/* Outer band first so the inner one reads as more likely. */}
        <Area
          dataKey="band95"
          stroke="none"
          fill={ACCENT_SOFT}
          fillOpacity={0.35}
          isAnimationActive={false}
          name="95% range"
        />
        <Area
          dataKey="band80"
          stroke="none"
          fill={ACCENT_SOFT}
          fillOpacity={0.7}
          isAnimationActive={false}
          name="80% range"
        />

        {crossesBreakEven && (
          <ReferenceLine
            y={0}
            stroke="#b06a1e"
            strokeWidth={1.5}
            strokeDasharray="5 5"
            label={{
              value: "break-even",
              position: "insideTopLeft",
              fill: "#b06a1e",
              fontSize: 11.5,
              fontWeight: 600,
            }}
          />
        )}

        <Line
          type="monotone"
          dataKey="actual"
          stroke={ACCENT}
          strokeWidth={2.5}
          dot={false}
          connectNulls={false}
          isAnimationActive
          animationDuration={520}
          name="actual"
        />
        <Line
          type="monotone"
          dataKey="mean"
          stroke="#c74722"
          strokeWidth={2.2}
          strokeDasharray="6 5"
          dot={false}
          connectNulls={false}
          isAnimationActive={false}
          name="projected"
        />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

/* --- baskets: what gets bought together -------------------------------- */

function BasketChart({ finding }: { finding: Finding }) {
  const bars = (finding.chart_data?.bars ?? []) as {
    label: string;
    value: number;
    share: number;
  }[];
  if (!bars.length) return <Empty label="No pairings stood out." />;

  // Values are lift multiples, not money, so 1.0 is the "no association"
  // baseline and the axis has to be read against it.
  return (
    <ResponsiveContainer width="100%" height={Math.max(150, bars.length * 40)}>
      <BarChart
        data={bars}
        layout="vertical"
        margin={{ top: 4, right: 52, bottom: 4, left: 8 }}
      >
        <XAxis type="number" hide domain={[0, "dataMax"]} />
        <YAxis
          type="category"
          dataKey="label"
          {...axis}
          width={190}
          tick={{ fill: "#211c15", fontSize: 12.5, fontFamily: "Albert Sans" }}
        />
        <Tooltip
          {...tooltipStyle}
          formatter={(v: number, _name, entry: any) => [
            `${v.toFixed(1)}x more often than chance · in ${(
              (entry?.payload?.share ?? 0) * 100
            ).toFixed(1)}% of baskets`,
            "lift",
          ]}
        />
        <ReferenceLine x={1} stroke={GREY} strokeDasharray="4 4" />
        <Bar dataKey="value" radius={[6, 6, 6, 6]} animationDuration={520}>
          {bars.map((_, i) => (
            <Cell key={i} fill={RAMP[Math.min(i, RAMP.length - 1)]} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

/* --- repeat vs new: the composition shift ------------------------------ */

function RepeatVsNewChart({ finding }: { finding: Finding }) {
  const series = (finding.chart_data?.series ?? []) as {
    period: string;
    repeat: number;
    new: number;
  }[];
  if (series.length < 2) return <Empty label="Not enough history yet." />;

  return (
    <ResponsiveContainer width="100%" height={230}>
      <ComposedChart data={series} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
        <XAxis dataKey="period" {...axis} minTickGap={40} />
        <YAxis {...axis} width={62} tickFormatter={(v) => compact(v)} />
        <Tooltip
          {...tooltipStyle}
          formatter={(v: number, name) => [
            money(v),
            name === "new" ? "first-time" : "returning",
          ]}
        />
        <Area
          type="monotone"
          dataKey="repeat"
          stackId="1"
          stroke={ACCENT}
          fill={ACCENT}
          fillOpacity={0.85}
          isAnimationActive
          animationDuration={520}
          name="returning"
        />
        <Area
          type="monotone"
          dataKey="new"
          stackId="1"
          stroke={ACCENT_SOFT}
          fill={ACCENT_SOFT}
          fillOpacity={0.9}
          isAnimationActive
          animationDuration={520}
          name="new"
        />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

/* --- RFM: who your champions and at-risk customers are ----------------- */

const SEGMENT_COLOUR: Record<string, string> = {
  Champions: GOOD,
  Loyal: "#8fcdb4",
  New: ACCENT,
  "At risk": "#e9a23b",
  Lost: GREY,
  Occasional: "#e5dfd5",
};

function SegmentQuadrant({ finding }: { finding: Finding }) {
  const customers = (finding.chart_data?.customers ?? []) as {
    recency: number;
    frequency: number;
    spend: number;
    segment: string;
  }[];
  const segments = (finding.chart_data?.segments ?? []) as {
    segment: string;
    customers: number;
    share: number;
  }[];
  if (!customers.length) return <Empty label="Not enough customers to group." />;

  // Recency inverted so "bought recently" sits on the right, which is how a
  // reader expects better to read.
  const maxRecency = Math.max(...customers.map((c) => c.recency), 1);
  const data = customers.map((c) => ({
    ...c,
    recentness: maxRecency - c.recency,
  }));

  return (
    <div style={{ display: "flex", gap: 26, flexWrap: "wrap", alignItems: "center" }}>
      <div style={{ flex: "1 1 320px", minWidth: 280 }}>
        <ResponsiveContainer width="100%" height={250}>
          <ScatterChart margin={{ top: 12, right: 18, bottom: 26, left: 6 }}>
            <XAxis
              type="number"
              dataKey="recentness"
              {...axis}
              tick={false}
              label={{
                value: "bought recently →",
                position: "insideBottom",
                offset: -12,
                fill: INK_LIGHT,
                fontSize: 11.5,
              }}
            />
            <YAxis
              type="number"
              dataKey="frequency"
              {...axis}
              width={44}
              label={{
                value: "buys often →",
                angle: -90,
                position: "insideLeft",
                fill: INK_LIGHT,
                fontSize: 11.5,
              }}
            />
            <ZAxis type="number" dataKey="spend" range={[30, 300]} />
            <Tooltip
              {...tooltipStyle}
              content={({ payload }) => {
                const p = payload?.[0]?.payload;
                if (!p) return null;
                return (
                  <div
                    style={{
                      background: "#fff",
                      border: "1px solid #f0ebe3",
                      borderRadius: 12,
                      padding: "10px 12px",
                      fontSize: 13,
                      boxShadow: "0 8px 24px rgba(33,28,21,0.10)",
                    }}
                  >
                    <strong>{p.segment}</strong>
                    <div style={{ color: INK_LIGHT }}>
                      {p.frequency} orders · {money(p.spend)} · last bought{" "}
                      {p.recency} days before the file ends
                    </div>
                  </div>
                );
              }}
            />
            <Scatter data={data} animationDuration={520}>
              {data.map((c, i) => (
                <Cell
                  key={i}
                  fill={SEGMENT_COLOUR[c.segment] ?? GREY}
                  fillOpacity={0.75}
                />
              ))}
            </Scatter>
          </ScatterChart>
        </ResponsiveContainer>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8, fontSize: 14 }}>
        {segments.map((s) => (
          <div key={s.segment} style={{ display: "flex", alignItems: "center", gap: 9 }}>
            <span
              style={{
                width: 11,
                height: 11,
                borderRadius: 4,
                background: SEGMENT_COLOUR[s.segment] ?? GREY,
              }}
            />
            <span style={{ minWidth: 92 }}>{s.segment}</span>
            <span style={{ color: INK_LIGHT }}>
              {s.customers} · {Math.round(s.share * 100)}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* --- cohort retention: does each intake stick? ------------------------- */

function CohortHeatmap({ finding }: { finding: Finding }) {
  const cohorts = (finding.chart_data?.cohorts ?? []) as {
    cohort: string;
    size: number;
    rates: { age: number; retention: number }[];
  }[];
  if (!cohorts.length) return <Empty label="Not enough cohorts to compare." />;

  const shown = cohorts.slice(-10);
  const maxAge = Math.max(
    ...shown.flatMap((c) => c.rates.map((r) => r.age)),
    1
  );
  const ages = Array.from({ length: Math.min(maxAge + 1, 13) }, (_, i) => i);

  const cell = 40;
  const rowH = 26;
  const labelW = 78;
  const width = labelW + ages.length * cell + 8;
  const height = shown.length * rowH + 34;

  const shade = (r: number) =>
    `rgba(232, 90, 50, ${Math.max(0.08, Math.min(r, 1)) * 0.9})`;

  return (
    <div style={{ overflowX: "auto" }}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        style={{ width: "100%", minWidth: 420, height: "auto" }}
      >
        {ages.map((age) => (
          <text
            key={`h-${age}`}
            x={labelW + age * cell + cell / 2 - 2}
            y={12}
            textAnchor="middle"
            fontFamily="Albert Sans"
            fontSize={10.5}
            fill={INK_LIGHT}
          >
            {age === 0 ? "start" : `+${age}`}
          </text>
        ))}
        {shown.map((cohort, row) => {
          const y = 22 + row * rowH;
          const byAge = new Map(cohort.rates.map((r) => [r.age, r.retention]));
          return (
            <g key={cohort.cohort}>
              <text
                x={labelW - 10}
                y={y + rowH / 2 - 1}
                textAnchor="end"
                dominantBaseline="middle"
                fontFamily="Albert Sans"
                fontSize={11}
                fill="#211c15"
              >
                {cohort.cohort}
              </text>
              {ages.map((age) => {
                const value = byAge.get(age);
                if (value === undefined) return null;
                return (
                  <g key={age}>
                    <rect
                      x={labelW + age * cell}
                      y={y}
                      width={cell - 3}
                      height={rowH - 4}
                      rx={4}
                      fill={shade(value)}
                    />
                    {cell > 30 && (
                      <text
                        x={labelW + age * cell + (cell - 3) / 2}
                        y={y + (rowH - 4) / 2}
                        textAnchor="middle"
                        dominantBaseline="middle"
                        fontFamily="Albert Sans"
                        fontSize={9.5}
                        fontWeight={600}
                        fill={value > 0.55 ? "#fff" : "#6e675c"}
                      >
                        {Math.round(value * 100)}
                      </text>
                    )}
                  </g>
                );
              })}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/* --- goal pace: will the target be hit? -------------------------------- */

function ProgressArc({ finding }: { finding: Finding }) {
  const d = finding.chart_data ?? {};
  const target = Number(d.target) || 0;
  const actual = Number(d.actual) || 0;
  const projected = Number(d.projected) || 0;
  const pace = (d.pace ?? []) as {
    period: string;
    actual: number;
    needed: number;
    ahead: boolean;
  }[];
  if (!target) return <Empty label="No target set." />;

  // A 180 degree arc. Banked so far is solid; the projection is a marker, so
  // the two are never confused for one another.
  const R = 100;
  const CX = 130;
  const CY = 140;
  const clampedActual = Math.max(0, Math.min(actual / target, 1));
  const clampedProjected = Math.max(0, Math.min(projected / target, 1.2));

  const point = (fraction: number) => {
    const angle = Math.PI * (1 - Math.min(fraction, 1));
    return [CX + R * Math.cos(angle), CY - R * Math.sin(angle)];
  };
  const [ax, ay] = point(clampedActual);
  const [px, py] = point(Math.min(clampedProjected, 1));
  const largeArc = clampedActual > 0.5 ? 1 : 0;
  const onTrack = projected >= target;

  return (
    <div style={{ display: "flex", gap: 30, flexWrap: "wrap", alignItems: "center" }}>
      <svg viewBox="0 0 260 165" style={{ width: 260, flex: "0 0 auto" }}>
        <path
          d={`M ${CX - R} ${CY} A ${R} ${R} 0 0 1 ${CX + R} ${CY}`}
          fill="none"
          stroke="#f0e7df"
          strokeWidth={18}
          strokeLinecap="round"
        />
        {clampedActual > 0.001 && (
          <path
            d={`M ${CX - R} ${CY} A ${R} ${R} 0 ${largeArc} 1 ${ax} ${ay}`}
            fill="none"
            stroke={onTrack ? GOOD : ACCENT}
            strokeWidth={18}
            strokeLinecap="round"
          />
        )}
        {/* Where the projection lands, distinct from what is banked. */}
        <circle
          cx={px}
          cy={py}
          r={7}
          fill="#fff"
          stroke={onTrack ? "#177e5b" : "#b06a1e"}
          strokeWidth={3}
        />
        <text
          x={CX}
          y={CY - 28}
          textAnchor="middle"
          fontFamily="Sora"
          fontSize={28}
          fontWeight={800}
          fill="#211c15"
        >
          {compact(actual)}
        </text>
        <text
          x={CX}
          y={CY - 8}
          textAnchor="middle"
          fontFamily="Albert Sans"
          fontSize={12}
          fill={INK_LIGHT}
        >
          of {compact(target)} · {Math.round((actual / target) * 100)}%
        </text>
      </svg>

      <div style={{ display: "flex", flexDirection: "column", gap: 12, flex: "1 1 240px" }}>
        <div style={{ display: "flex", gap: 22, flexWrap: "wrap" }}>
          <div>
            <div style={{ font: "700 20px Sora" }}>{compact(projected)}</div>
            <div style={{ fontSize: 12.5, color: INK_LIGHT }}>projected total</div>
          </div>
          <div>
            <div
              style={{
                font: "700 20px Sora",
                color: onTrack ? "#177e5b" : "#c74722",
              }}
            >
              {projected >= target ? "+" : "−"}
              {compact(Math.abs(target - projected))}
            </div>
            <div style={{ fontSize: 12.5, color: INK_LIGHT }}>
              {projected >= target ? "surplus at this pace" : "gap at this pace"}
            </div>
          </div>
        </div>

        {pace.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
            {pace.slice(-6).map((row) => {
              const width = Math.max(
                2,
                Math.min((row.actual / Math.max(row.needed, 1)) * 100, 130)
              );
              return (
                <div
                  key={row.period}
                  style={{ display: "flex", alignItems: "center", gap: 10 }}
                >
                  <span
                    style={{
                      fontSize: 12,
                      fontWeight: 600,
                      width: 52,
                      color: INK_LIGHT,
                    }}
                  >
                    {row.period.slice(0, 7)}
                  </span>
                  <div
                    style={{
                      flex: 1,
                      position: "relative",
                      height: 14,
                      borderRadius: 999,
                      background: "#f0e7df",
                    }}
                  >
                    <div
                      style={{
                        position: "absolute",
                        inset: "0 auto 0 0",
                        width: `${Math.min(width, 100)}%`,
                        borderRadius: 999,
                        background: row.ahead ? GOOD : ACCENT,
                      }}
                    />
                    {/* The pace the target needs. */}
                    <div
                      style={{
                        position: "absolute",
                        left: "100%",
                        top: -3,
                        bottom: -3,
                        width: 2.5,
                        borderRadius: 2,
                        background: "#211c15",
                        transform: "translateX(-2px)",
                      }}
                    />
                  </div>
                </div>
              );
            })}
            <div style={{ fontSize: 11.5, color: INK_LIGHT }}>
              The dark tick is the pace the target needs.
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/* --- router ------------------------------------------------------------ */

export default function Chart({ finding }: { finding: Finding }) {
  // Baskets share bar_horizontal with rankings but carry lift multiples rather
  // than money, so they need their own reading of the same shape.
  if (finding.type === "basket") return <BasketChart finding={finding} />;

  switch (finding.chart) {
    case "line_with_band":
      return <TrendChart finding={finding} />;
    case "bar_horizontal":
      return <RankingChart finding={finding} />;
    case "donut":
    case "treemap":
      return <ConcentrationChart finding={finding} />;
    case "scatter":
      return <TensionChart finding={finding} />;
    case "waterfall":
      return <WaterfallChart finding={finding} />;
    case "grouped_bars":
      return <SegmentChart finding={finding} />;
    case "correlation_heatmap":
      return <CorrelationChart finding={finding} />;
    case "forecast_fan":
      return <ForecastChart finding={finding} />;
    case "stacked_area":
      return <RepeatVsNewChart finding={finding} />;
    case "quadrant":
      return <SegmentQuadrant finding={finding} />;
    case "cohort_heatmap":
      return <CohortHeatmap finding={finding} />;
    case "progress_arc":
      return <ProgressArc finding={finding} />;
    default:
      // A finding type whose chart is not built yet still shows its sentence
      // rather than an empty frame or a wrong picture.
      return null;
  }
}
