/**
 * Design tokens — the single source of truth for colour, elevation, radius and
 * motion. Components import from here rather than hard-coding hex values so a
 * palette change lands everywhere at once.
 *
 * Scale: 8px spacing base (MUI `spacing(1)` === 8px).
 */

export type Mode = 'light' | 'dark';

/** Radius scale. Cards 16, controls 12 — per the design system. */
export const radii = {
  xs: 6,
  sm: 8,
  control: 12,
  card: 16,
  lg: 20,
  pill: 999,
} as const;

/** Brand ramp. `main` is the 500 step used for primary actions. */
export const blue = {
  50: '#EFF6FF',
  100: '#DBEAFE',
  200: '#BFDBFE',
  300: '#93C5FD',
  400: '#60A5FA',
  500: '#2563EB',
  600: '#1D4ED8',
  700: '#1E40AF',
  800: '#1E3A8A',
  900: '#172554',
} as const;

export const slate = {
  50: '#F8FAFC',
  100: '#F1F5F9',
  200: '#E2E8F0',
  300: '#CBD5E1',
  400: '#94A3B8',
  500: '#64748B',
  600: '#475569',
  700: '#334155',
  800: '#1E293B',
  900: '#0F172A',
  950: '#020617',
} as const;

export const semantic = {
  success: '#22C55E',
  successDark: '#16A34A',
  warning: '#F59E0B',
  warningDark: '#D97706',
  danger: '#EF4444',
  dangerDark: '#DC2626',
  info: '#0EA5E9',
  violet: '#8B5CF6',
  teal: '#14B8A6',
  pink: '#EC4899',
  amber: '#F59E0B',
} as const;

/** Categorical series colours for charts — ordered for maximum adjacent contrast. */
export const chartSeries = [
  blue[500],
  semantic.teal,
  semantic.violet,
  semantic.amber,
  semantic.pink,
  '#0EA5E9',
  '#84CC16',
  '#F97316',
] as const;

interface Surfaces {
  /** App canvas behind all cards. */
  canvas: string;
  /** Default card / panel background. */
  surface: string;
  /** Raised surface: menus, popovers, command palette. */
  raised: string;
  /** Inset wells: code blocks, table headers, empty slots. */
  sunken: string;
  /** Hairline borders — kept very light; we lean on shadow, not stroke. */
  border: string;
  /** Slightly stronger border for focus/active affordances. */
  borderStrong: string;
  /**
   * Reserved for elevated cards (Surface). Cards lean on shadow, not stroke:
   * light mode goes fully transparent at rest; dark mode keeps a faint line
   * for edge definition against the near-black canvas.
   */
  cardBorder: string;
  /** Sidebar background. */
  nav: string;
  /** Hover wash for rows and nav items. */
  hover: string;
  /** Selected wash. */
  selected: string;
}

export const surfaces: Record<Mode, Surfaces> = {
  light: {
    canvas: '#F8FAFC',
    surface: '#FFFFFF',
    raised: '#FFFFFF',
    sunken: '#F8FAFC',
    border: '#EAEEF3',
    borderStrong: '#DDE3EB',
    cardBorder: 'transparent',
    nav: '#FFFFFF',
    hover: 'rgba(15, 23, 42, 0.04)',
    selected: 'rgba(37, 99, 235, 0.08)',
  },
  dark: {
    canvas: '#0A0E17',
    surface: '#111827',
    raised: '#161E2E',
    sunken: '#0D131F',
    border: 'rgba(148, 163, 184, 0.14)',
    borderStrong: 'rgba(148, 163, 184, 0.24)',
    cardBorder: 'rgba(148, 163, 184, 0.10)',
    nav: '#0D131F',
    hover: 'rgba(148, 163, 184, 0.08)',
    selected: 'rgba(59, 130, 246, 0.16)',
  },
};

export const text: Record<Mode, { primary: string; secondary: string; disabled: string }> = {
  light: {
    primary: '#0F172A',
    secondary: '#64748B',
    disabled: '#94A3B8',
  },
  dark: {
    primary: '#F1F5F9',
    secondary: '#94A3B8',
    disabled: '#64748B',
  },
};

/**
 * Layered shadows. Each step stacks a tight contact shadow with a wider ambient
 * one, which is what separates "premium" from the single flat drop shadow.
 */
export const elevation: Record<Mode, Record<'xs' | 'sm' | 'md' | 'lg' | 'xl', string>> = {
  light: {
    xs: '0 1px 2px 0 rgba(15, 23, 42, 0.05)',
    sm: '0 1px 2px 0 rgba(15, 23, 42, 0.06), 0 1px 3px 0 rgba(15, 23, 42, 0.05)',
    md: '0 2px 4px -1px rgba(15, 23, 42, 0.05), 0 6px 16px -4px rgba(15, 23, 42, 0.08)',
    lg: '0 4px 8px -2px rgba(15, 23, 42, 0.06), 0 16px 32px -8px rgba(15, 23, 42, 0.12)',
    xl: '0 8px 16px -4px rgba(15, 23, 42, 0.08), 0 32px 64px -16px rgba(15, 23, 42, 0.18)',
  },
  dark: {
    xs: '0 1px 2px 0 rgba(0, 0, 0, 0.4)',
    sm: '0 1px 2px 0 rgba(0, 0, 0, 0.5), 0 1px 3px 0 rgba(0, 0, 0, 0.4)',
    md: '0 2px 4px -1px rgba(0, 0, 0, 0.5), 0 6px 16px -4px rgba(0, 0, 0, 0.5)',
    lg: '0 4px 8px -2px rgba(0, 0, 0, 0.5), 0 16px 32px -8px rgba(0, 0, 0, 0.55)',
    xl: '0 8px 16px -4px rgba(0, 0, 0, 0.5), 0 32px 64px -16px rgba(0, 0, 0, 0.6)',
  },
};

/** Focus ring used on every interactive element for WCAG-visible keyboard focus. */
export const focusRing = (mode: Mode) =>
  `0 0 0 3px ${mode === 'light' ? 'rgba(37, 99, 235, 0.24)' : 'rgba(96, 165, 250, 0.32)'}`;

/**
 * Motion. Short and eased — animation should feel like the UI responding, never
 * like the UI performing. Every duration here is under 320ms.
 */
export const motion = {
  fast: '120ms',
  base: '180ms',
  slow: '280ms',
  /** Standard easing: quick out, gentle settle. */
  ease: 'cubic-bezier(0.32, 0.72, 0, 1)',
  /** For elements entering the viewport. */
  entrance: 'cubic-bezier(0.16, 1, 0.3, 1)',
} as const;

export const layout = {
  sidebarWidth: 264,
  sidebarCollapsedWidth: 76,
  /** Gutter between the floating sidebar and the viewport edge / content column. */
  sidebarInset: 12,
  topbarHeight: 64,
  /** Max width of a reading/form column. */
  formWidth: 760,
  contentMaxWidth: 1600,
} as const;

export const fontStack = [
  '"Inter"',
  '"Inter var"',
  '"Geist"',
  '-apple-system',
  'BlinkMacSystemFont',
  '"Segoe UI"',
  'Roboto',
  '"Helvetica Neue"',
  'Arial',
  'sans-serif',
].join(', ');

export const monoStack = [
  '"Geist Mono"',
  '"JetBrains Mono"',
  'ui-monospace',
  'SFMono-Regular',
  '"SF Mono"',
  'Menlo',
  'Consolas',
  'monospace',
].join(', ');

/** Tokens bundle for the active mode, consumed via `useTokens()`. */
export function tokensFor(mode: Mode) {
  return {
    mode,
    surfaces: surfaces[mode],
    text: text[mode],
    elevation: elevation[mode],
    focusRing: focusRing(mode),
    radii,
    motion,
    layout,
    chartSeries,
    semantic,
  };
}

export type Tokens = ReturnType<typeof tokensFor>;
