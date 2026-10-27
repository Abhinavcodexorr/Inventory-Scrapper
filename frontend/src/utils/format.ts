import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime';

dayjs.extend(relativeTime);

/** Format ISO timestamps as DD MMM YYYY, HH:mm in local timezone. */
export function formatDate(value: string | null | undefined): string {
  if (!value) return '—';
  return dayjs(value).format('DD MMM YYYY, HH:mm');
}

export function formatDateShort(value: string | null | undefined): string {
  if (!value) return '—';
  return dayjs(value).format('DD MMM YYYY');
}

/** Format money from API decimal string — do not parse to float for display math. */
export function formatMoney(
  price: string | null | undefined,
  currency = 'CAD',
): string {
  if (price == null || price === '') return '—';
  const symbol = currency === 'USD' ? 'US$' : '$';
  const [whole, frac] = String(price).split('.');
  const withCommas = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  if (frac === undefined || frac === '00') return `${symbol}${withCommas}`;
  return `${symbol}${withCommas}.${frac}`;
}

/** Duration between started and finished — or, while still running (finished is null), between started and `now`. */
export function formatDuration(started: string, finished: string | null, now?: number): string {
  const end = finished ? dayjs(finished) : now != null ? dayjs(now) : null;
  if (!end) return '—';
  const ms = end.diff(dayjs(started));
  if (ms < 0) return '—';
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  const rem = sec % 60;
  if (min < 60) return `${min}m ${rem}s`;
  const hr = Math.floor(min / 60);
  return `${hr}h ${min % 60}m`;
}

/** Human-readable duration from a raw seconds value (e.g. dealer.last_sync_duration_seconds). */
export function formatSeconds(value: number | null | undefined): string {
  if (value == null || value < 0) return '—';
  const sec = Math.round(value);
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ${sec % 60}s`;
  return `${Math.floor(min / 60)}h ${min % 60}m`;
}

export function fieldOrPending(value: string | null | undefined): string {
  if (value == null || value === '') return 'Not captured';
  return value;
}

/** "3 minutes ago" — used wherever recency matters more than the exact clock time. */
export function formatRelative(value: string | null | undefined): string {
  if (!value) return 'Never';
  return dayjs(value).fromNow();
}

/** Compact date for chart axes, e.g. "12 Mar". */
export function formatAxisDate(value: string | null | undefined): string {
  if (!value) return '';
  return dayjs(value).format('D MMM');
}

/** Abbreviate large counts for tight spaces: 12400 → "12.4k". */
export function formatCompact(value: number | null | undefined): string {
  if (value == null) return '—';
  if (Math.abs(value) < 1000) return String(value);
  if (Math.abs(value) < 1_000_000) return `${(value / 1000).toFixed(value % 1000 === 0 ? 0 : 1)}k`;
  return `${(value / 1_000_000).toFixed(1)}M`;
}

/** Money as a bare rounded number for chart axes — no symbol, no decimals. */
export function formatMoneyCompact(price: string | number | null | undefined): string {
  if (price == null || price === '') return '—';
  const n = typeof price === 'number' ? price : Number(price);
  if (!Number.isFinite(n)) return '—';
  if (Math.abs(n) >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (Math.abs(n) >= 1000) return `$${Math.round(n / 1000)}k`;
  return `$${Math.round(n)}`;
}

/** Title for a vehicle built from whichever of year/make/model/trim exist. */
export function vehicleTitle(v: {
  year?: number | null;
  make?: string | null;
  model?: string | null;
  trim?: string | null;
  id?: number;
}): string {
  const title = [v.year, v.make, v.model, v.trim].filter(Boolean).join(' ');
  return title || (v.id != null ? `Vehicle #${v.id}` : 'Unknown vehicle');
}

/** Greeting keyed to the local hour — used in the dashboard hero. */
export function greeting(): string {
  const hour = dayjs().hour();
  if (hour < 12) return 'Good morning';
  if (hour < 18) return 'Good afternoon';
  return 'Good evening';
}

/** Percentage helper that treats a zero denominator as 0 rather than NaN. */
export function percent(numerator: number, denominator: number): number {
  if (!denominator) return 0;
  return (numerator / denominator) * 100;
}

/** Initials for an avatar, e.g. "Prairie Auto Group" → "PA". */
export function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return '?';
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
}

/** Deterministic accent colour for a name — stable across reloads. */
export function colorForName(name: string, palette: readonly string[]): string {
  let hash = 0;
  for (let i = 0; i < name.length; i += 1) hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  return palette[hash % palette.length];
}
