/** Number formatting for display only.
 *
 *  Nothing here computes anything. Every value shown was calculated by the
 *  engine; this only decides how many characters it takes up on screen.
 */

export function compact(value: number): string {
  if (!Number.isFinite(value)) return "—";
  const magnitude = Math.abs(value);
  if (magnitude >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(1)}b`;
  if (magnitude >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}m`;
  if (magnitude >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return value.toFixed(0);
}

export function money(value: number): string {
  if (!Number.isFinite(value)) return "—";
  return compact(value);
}

export function percent(value: number, digits = 0): string {
  if (!Number.isFinite(value)) return "—";
  return `${(Math.abs(value) * 100).toFixed(digits)}%`;
}

export function signedPercent(value: number, digits = 0): string {
  if (!Number.isFinite(value)) return "—";
  const sign = value < 0 ? "−" : "+";
  return `${sign}${(Math.abs(value) * 100).toFixed(digits)}%`;
}
