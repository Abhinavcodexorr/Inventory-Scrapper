import { alpha, createTheme, type Theme } from '@mui/material/styles';
import type { Shadows } from '@mui/material/styles';
import {
  blue,
  elevation,
  focusRing,
  fontStack,
  motion,
  radii,
  semantic,
  slate,
  surfaces,
  text,
  type Mode,
} from './tokens';

/**
 * MUI needs exactly 25 elevation steps. We map the five designed levels across
 * the range so any `elevation={n}` still lands on a token rather than Material's
 * stock shadows, which are far too heavy for this design language.
 */
function buildShadows(mode: Mode): Shadows {
  const e = elevation[mode];
  const ramp = [
    'none',
    e.xs,
    e.xs,
    e.sm,
    e.sm,
    e.sm,
    e.md,
    e.md,
    e.md,
    e.md,
    e.lg,
    e.lg,
    e.lg,
    e.lg,
    e.lg,
    e.lg,
    e.xl,
    e.xl,
    e.xl,
    e.xl,
    e.xl,
    e.xl,
    e.xl,
    e.xl,
    e.xl,
  ];
  return ramp as unknown as Shadows;
}

export function createAppTheme(mode: Mode, density: 'comfortable' | 'compact' = 'comfortable'): Theme {
  const s = surfaces[mode];
  const t = text[mode];
  const e = elevation[mode];
  const ring = focusRing(mode);
  const isLight = mode === 'light';
  const compact = density === 'compact';

  const primaryMain = isLight ? blue[500] : '#3B82F6';

  const base = createTheme({
    palette: {
      mode,
      common: { black: slate[950], white: '#FFFFFF' },
      primary: {
        main: primaryMain,
        light: isLight ? blue[400] : '#60A5FA',
        dark: isLight ? blue[600] : '#2563EB',
        contrastText: '#FFFFFF',
      },
      secondary: {
        main: semantic.violet,
        contrastText: '#FFFFFF',
      },
      success: { main: semantic.success, dark: semantic.successDark, contrastText: '#FFFFFF' },
      warning: { main: semantic.warning, dark: semantic.warningDark, contrastText: '#1A1206' },
      error: { main: semantic.danger, dark: semantic.dangerDark, contrastText: '#FFFFFF' },
      info: { main: semantic.info, contrastText: '#FFFFFF' },
      background: { default: s.canvas, paper: s.surface },
      text: { primary: t.primary, secondary: t.secondary, disabled: t.disabled },
      divider: s.border,
      action: {
        hover: s.hover,
        selected: s.selected,
        focus: s.selected,
        disabledOpacity: 0.4,
      },
      grey: slate as unknown as Record<string, string>,
    },
    shape: { borderRadius: radii.control },
    shadows: buildShadows(mode),
    spacing: 8,
    typography: {
      fontFamily: fontStack,
      // Tight, confident headings; generous body leading for scannability.
      h1: { fontSize: '2.25rem', fontWeight: 700, letterSpacing: '-0.03em', lineHeight: 1.15 },
      h2: { fontSize: '1.75rem', fontWeight: 700, letterSpacing: '-0.025em', lineHeight: 1.2 },
      h3: { fontSize: '1.5rem', fontWeight: 700, letterSpacing: '-0.022em', lineHeight: 1.25 },
      h4: { fontSize: '1.25rem', fontWeight: 660, letterSpacing: '-0.02em', lineHeight: 1.3 },
      h5: { fontSize: '1.0625rem', fontWeight: 640, letterSpacing: '-0.015em', lineHeight: 1.35 },
      h6: { fontSize: '0.9375rem', fontWeight: 640, letterSpacing: '-0.011em', lineHeight: 1.4 },
      subtitle1: { fontSize: '0.9375rem', fontWeight: 560, letterSpacing: '-0.008em' },
      subtitle2: { fontSize: '0.8125rem', fontWeight: 600, letterSpacing: '-0.005em' },
      body1: { fontSize: '0.9375rem', lineHeight: 1.6, letterSpacing: '-0.006em' },
      body2: { fontSize: '0.8438rem', lineHeight: 1.55, letterSpacing: '-0.003em' },
      caption: { fontSize: '0.75rem', lineHeight: 1.45, letterSpacing: '0' },
      overline: {
        fontSize: '0.6875rem',
        fontWeight: 620,
        letterSpacing: '0.06em',
        textTransform: 'uppercase',
        lineHeight: 1.4,
      },
      button: { fontSize: '0.875rem', fontWeight: 560, letterSpacing: '-0.006em', textTransform: 'none' },
    },
    transitions: {
      duration: { shortest: 120, shorter: 150, short: 180, standard: 220, complex: 280 },
      easing: { easeInOut: motion.ease, easeOut: motion.entrance, easeIn: motion.ease, sharp: motion.ease },
    },
  });

  return createTheme(base, {
    components: {
      MuiCssBaseline: {
        styleOverrides: {
          ':root': {
            colorScheme: mode,
          },
          html: {
            WebkitFontSmoothing: 'antialiased',
            MozOsxFontSmoothing: 'grayscale',
            textRendering: 'optimizeLegibility',
          },
          body: {
            backgroundColor: s.canvas,
            // Tabular figures everywhere means numeric columns never jitter as
            // they count up or refetch.
            fontVariantNumeric: 'tabular-nums',
          },
          '*::-webkit-scrollbar': { width: 10, height: 10 },
          '*::-webkit-scrollbar-track': { background: 'transparent' },
          '*::-webkit-scrollbar-thumb': {
            background: isLight ? 'rgba(15,23,42,0.16)' : 'rgba(148,163,184,0.24)',
            borderRadius: 999,
            border: '3px solid transparent',
            backgroundClip: 'content-box',
          },
          '*::-webkit-scrollbar-thumb:hover': {
            background: isLight ? 'rgba(15,23,42,0.28)' : 'rgba(148,163,184,0.4)',
            backgroundClip: 'content-box',
          },
          // One global honour of the OS reduced-motion setting.
          '@media (prefers-reduced-motion: reduce)': {
            '*, *::before, *::after': {
              animationDuration: '0.01ms !important',
              animationIterationCount: '1 !important',
              transitionDuration: '0.01ms !important',
              scrollBehavior: 'auto !important',
            },
          },
          '::selection': { background: alpha(primaryMain, 0.22) },
        },
      },

      MuiPaper: {
        defaultProps: { elevation: 0 },
        styleOverrides: {
          root: { backgroundImage: 'none' },
          rounded: { borderRadius: radii.card },
          outlined: { borderColor: s.border },
        },
      },

      MuiCard: {
        defaultProps: { elevation: 0 },
        styleOverrides: {
          root: {
            borderRadius: radii.card,
            border: `1px solid ${s.border}`,
            backgroundColor: s.surface,
            boxShadow: e.xs,
          },
        },
      },

      MuiCardContent: {
        styleOverrides: {
          root: { padding: compact ? 16 : 20, '&:last-child': { paddingBottom: compact ? 16 : 20 } },
        },
      },

      MuiButton: {
        defaultProps: { disableElevation: true, disableRipple: false },
        styleOverrides: {
          root: {
            borderRadius: radii.control,
            fontWeight: 560,
            paddingInline: 14,
            transition: `background-color ${motion.fast} ${motion.ease}, box-shadow ${motion.fast} ${motion.ease}, transform ${motion.fast} ${motion.ease}, border-color ${motion.fast} ${motion.ease}`,
            '&:focus-visible': { boxShadow: ring, outline: 'none' },
            // Hover lift + press settle. Transform only — no layout thrash.
            '&:hover': { transform: 'translateY(-1px)' },
            '&:active': { transform: 'translateY(0)', transitionDuration: '60ms' },
            '&.Mui-disabled': { transform: 'none' },
          },
          sizeSmall: { paddingInline: 10, minHeight: 32, fontSize: '0.8125rem' },
          sizeMedium: { minHeight: 40 },
          sizeLarge: { minHeight: 46, paddingInline: 20, fontSize: '0.9375rem' },
          contained: {
            boxShadow: `${e.xs}, inset 0 1px 0 0 ${alpha('#FFFFFF', 0.14)}`,
            '&:hover': { boxShadow: e.md, transform: 'translateY(-1px)' },
            '&:active': { boxShadow: e.xs },
          },
          containedPrimary: {
            background: `linear-gradient(180deg, ${alpha(primaryMain, 1)} 0%, ${isLight ? blue[600] : '#2563EB'} 100%)`,
            '&:hover': { background: `linear-gradient(180deg, ${isLight ? blue[600] : '#2563EB'} 0%, ${isLight ? blue[700] : '#1D4ED8'} 100%)` },
          },
          outlined: {
            borderColor: s.borderStrong,
            backgroundColor: s.surface,
            color: t.primary,
            '&:hover': { borderColor: alpha(primaryMain, 0.5), backgroundColor: s.hover },
          },
          text: {
            color: t.secondary,
            '&:hover': { backgroundColor: s.hover, color: t.primary },
          },
        },
      },

      MuiIconButton: {
        styleOverrides: {
          root: {
            borderRadius: radii.sm + 2,
            color: t.secondary,
            transition: `background-color ${motion.fast} ${motion.ease}, color ${motion.fast} ${motion.ease}, transform ${motion.fast} ${motion.ease}`,
            '&:hover': { backgroundColor: s.hover, color: t.primary },
            '&:active': { transform: 'scale(0.94)' },
            '&:focus-visible': { boxShadow: ring, outline: 'none' },
          },
        },
      },

      MuiToggleButton: {
        styleOverrides: {
          root: {
            borderRadius: radii.control,
            borderColor: s.border,
            textTransform: 'none',
            fontWeight: 560,
            color: t.secondary,
            '&.Mui-selected': {
              backgroundColor: s.surface,
              color: t.primary,
              boxShadow: e.xs,
            },
            '&:focus-visible': { boxShadow: ring, outline: 'none' },
          },
        },
      },

      MuiToggleButtonGroup: {
        styleOverrides: {
          root: {
            backgroundColor: s.sunken,
            borderRadius: radii.control,
            padding: 3,
            gap: 3,
          },
          grouped: {
            border: 'none !important',
            borderRadius: `${radii.sm + 1}px !important`,
            minHeight: 30,
            paddingInline: 12,
          },
        },
      },

      MuiOutlinedInput: {
        styleOverrides: {
          root: {
            borderRadius: radii.control,
            backgroundColor: s.surface,
            transition: `box-shadow ${motion.fast} ${motion.ease}, border-color ${motion.fast} ${motion.ease}`,
            '& .MuiOutlinedInput-notchedOutline': { borderColor: s.borderStrong },
            '&:hover .MuiOutlinedInput-notchedOutline': { borderColor: isLight ? slate[300] : 'rgba(148,163,184,0.34)' },
            '&.Mui-focused': { boxShadow: ring },
            '&.Mui-focused .MuiOutlinedInput-notchedOutline': { borderColor: primaryMain, borderWidth: 1 },
            '&.Mui-error.Mui-focused': { boxShadow: `0 0 0 3px ${alpha(semantic.danger, 0.2)}` },
          },
          input: {
            '&::placeholder': { color: t.disabled, opacity: 1 },
          },
          inputSizeSmall: { paddingTop: 9, paddingBottom: 9 },
        },
      },

      MuiInputLabel: {
        styleOverrides: {
          root: { fontSize: '0.875rem', '&.Mui-focused': { color: primaryMain } },
        },
      },

      MuiFormHelperText: {
        styleOverrides: {
          root: { marginLeft: 2, marginTop: 6, fontSize: '0.75rem' },
        },
      },

      MuiSelect: {
        defaultProps: { MenuProps: { elevation: 0 } },
      },

      MuiMenu: {
        styleOverrides: {
          paper: {
            borderRadius: radii.card - 2,
            border: `1px solid ${s.border}`,
            boxShadow: e.lg,
            backgroundColor: s.raised,
            marginTop: 6,
            padding: 6,
            minWidth: 180,
          },
          list: { padding: 0 },
        },
      },

      MuiMenuItem: {
        styleOverrides: {
          root: {
            borderRadius: radii.sm,
            fontSize: '0.875rem',
            minHeight: 36,
            paddingInline: 10,
            transition: `background-color ${motion.fast} ${motion.ease}`,
            '&.Mui-selected': { backgroundColor: s.selected, color: primaryMain, fontWeight: 560 },
            '&.Mui-selected:hover': { backgroundColor: s.selected },
          },
        },
      },

      MuiListItemIcon: {
        styleOverrides: { root: { minWidth: 32, color: 'inherit' } },
      },

      MuiPopover: {
        styleOverrides: {
          paper: {
            borderRadius: radii.card - 2,
            border: `1px solid ${s.border}`,
            boxShadow: e.lg,
            backgroundColor: s.raised,
          },
        },
      },

      MuiTooltip: {
        defaultProps: { arrow: true, enterDelay: 400, enterNextDelay: 200 },
        styleOverrides: {
          tooltip: {
            backgroundColor: isLight ? slate[900] : slate[800],
            color: '#FFFFFF',
            fontSize: '0.75rem',
            fontWeight: 500,
            borderRadius: radii.sm,
            paddingInline: 10,
            paddingBlock: 6,
            boxShadow: e.md,
          },
          arrow: { color: isLight ? slate[900] : slate[800] },
        },
      },

      MuiChip: {
        styleOverrides: {
          root: {
            borderRadius: radii.sm,
            fontWeight: 560,
            fontSize: '0.75rem',
            height: 24,
          },
          label: { paddingInline: 8 },
          outlined: { borderColor: s.border },
        },
      },

      MuiDialog: {
        defaultProps: { scroll: 'paper' },
        styleOverrides: {
          paper: {
            borderRadius: radii.card + 4,
            border: `1px solid ${s.border}`,
            boxShadow: e.xl,
            backgroundImage: 'none',
          },
        },
      },

      MuiBackdrop: {
        styleOverrides: {
          root: {
            // Blurred backdrop — the modal reads as a layer above the app, not a
            // sheet pasted over it.
            backgroundColor: isLight ? 'rgba(15, 23, 42, 0.32)' : 'rgba(2, 6, 23, 0.6)',
            backdropFilter: 'blur(6px) saturate(120%)',
          },
          invisible: { backgroundColor: 'transparent', backdropFilter: 'none' },
        },
      },

      MuiDialogTitle: {
        styleOverrides: {
          root: { padding: '24px 24px 8px', fontSize: '1.125rem', fontWeight: 660, letterSpacing: '-0.02em' },
        },
      },
      MuiDialogContent: { styleOverrides: { root: { padding: '0 24px 8px' } } },
      MuiDialogActions: {
        styleOverrides: {
          root: {
            padding: 20,
            gap: 8,
            borderTop: `1px solid ${s.border}`,
            marginTop: 16,
            backgroundColor: s.sunken,
            borderBottomLeftRadius: radii.card + 4,
            borderBottomRightRadius: radii.card + 4,
          },
        },
      },

      MuiTabs: {
        styleOverrides: {
          root: { minHeight: 40 },
          indicator: { height: 2, borderRadius: 2 },
          flexContainer: { gap: 4 },
        },
      },

      MuiTab: {
        styleOverrides: {
          root: {
            textTransform: 'none',
            fontWeight: 560,
            fontSize: '0.875rem',
            minHeight: 40,
            padding: '8px 12px',
            color: t.secondary,
            borderRadius: `${radii.sm}px ${radii.sm}px 0 0`,
            transition: `color ${motion.fast} ${motion.ease}, background-color ${motion.fast} ${motion.ease}`,
            '&:hover': { color: t.primary, backgroundColor: s.hover },
            '&.Mui-selected': { color: primaryMain, fontWeight: 600 },
            '&:focus-visible': { boxShadow: ring, outline: 'none' },
          },
        },
      },

      MuiAlert: {
        styleOverrides: {
          root: {
            borderRadius: radii.control,
            border: '1px solid',
            fontSize: '0.875rem',
            alignItems: 'flex-start',
            padding: '12px 16px',
          },
          icon: { paddingTop: 2, marginRight: 12 },
          standardInfo: {
            backgroundColor: alpha(semantic.info, isLight ? 0.07 : 0.14),
            borderColor: alpha(semantic.info, isLight ? 0.24 : 0.32),
            color: isLight ? '#075985' : '#BAE6FD',
          },
          standardSuccess: {
            backgroundColor: alpha(semantic.success, isLight ? 0.08 : 0.14),
            borderColor: alpha(semantic.success, isLight ? 0.26 : 0.32),
            color: isLight ? '#166534' : '#BBF7D0',
          },
          standardWarning: {
            backgroundColor: alpha(semantic.warning, isLight ? 0.1 : 0.14),
            borderColor: alpha(semantic.warning, isLight ? 0.3 : 0.34),
            color: isLight ? '#92400E' : '#FDE68A',
          },
          standardError: {
            backgroundColor: alpha(semantic.danger, isLight ? 0.07 : 0.14),
            borderColor: alpha(semantic.danger, isLight ? 0.26 : 0.34),
            color: isLight ? '#991B1B' : '#FECACA',
          },
        },
      },

      MuiLinearProgress: {
        styleOverrides: {
          root: { height: 6, borderRadius: 999, backgroundColor: isLight ? slate[200] : 'rgba(148,163,184,0.16)' },
          bar: { borderRadius: 999 },
        },
      },

      MuiSkeleton: {
        defaultProps: { animation: 'wave' },
        styleOverrides: {
          root: { backgroundColor: isLight ? 'rgba(15,23,42,0.06)' : 'rgba(148,163,184,0.1)', borderRadius: radii.sm },
          rectangular: { borderRadius: radii.control },
        },
      },

      MuiDivider: {
        styleOverrides: { root: { borderColor: s.border } },
      },

      MuiListItemButton: {
        styleOverrides: {
          root: {
            borderRadius: radii.control,
            transition: `background-color ${motion.fast} ${motion.ease}, color ${motion.fast} ${motion.ease}`,
            '&:focus-visible': { boxShadow: ring, outline: 'none' },
          },
        },
      },

      MuiTableCell: {
        styleOverrides: {
          root: {
            borderBottomColor: s.border,
            fontSize: '0.8438rem',
            padding: compact ? '8px 12px' : '12px 16px',
          },
          head: {
            fontWeight: 600,
            fontSize: '0.75rem',
            letterSpacing: '0.02em',
            color: t.secondary,
            backgroundColor: s.sunken,
            whiteSpace: 'nowrap',
          },
        },
      },

      MuiSwitch: {
        styleOverrides: {
          root: { width: 40, height: 24, padding: 0 },
          switchBase: {
            padding: 3,
            '&.Mui-checked': { transform: 'translateX(16px)', color: '#fff' },
            '&.Mui-checked + .MuiSwitch-track': { opacity: 1, backgroundColor: primaryMain },
            '&:focus-visible + .MuiSwitch-track': { boxShadow: ring },
          },
          thumb: { width: 18, height: 18, boxShadow: e.xs },
          track: { borderRadius: 999, opacity: 1, backgroundColor: isLight ? slate[300] : 'rgba(148,163,184,0.3)' },
        },
      },

      MuiAccordion: {
        defaultProps: { elevation: 0, disableGutters: true },
        styleOverrides: {
          root: {
            border: `1px solid ${s.border}`,
            borderRadius: `${radii.control}px !important`,
            backgroundColor: s.surface,
            '&::before': { display: 'none' },
          },
        },
      },
      MuiAccordionSummary: {
        styleOverrides: {
          root: { borderRadius: radii.control, minHeight: 52, paddingInline: 16 },
          content: { marginBlock: 12 },
        },
      },
      MuiAccordionDetails: { styleOverrides: { root: { padding: '0 16px 16px' } } },

      MuiAvatar: {
        styleOverrides: {
          root: { fontSize: '0.8125rem', fontWeight: 600 },
          rounded: { borderRadius: radii.sm + 2 },
        },
      },

      MuiBadge: {
        styleOverrides: {
          badge: { fontSize: '0.6875rem', fontWeight: 640, minWidth: 18, height: 18, padding: '0 5px' },
        },
      },

      MuiLink: {
        defaultProps: { underline: 'hover' },
        styleOverrides: {
          root: {
            fontWeight: 540,
            textDecorationColor: alpha(primaryMain, 0.4),
            transition: `color ${motion.fast} ${motion.ease}`,
            borderRadius: 4,
            '&:focus-visible': { boxShadow: ring, outline: 'none' },
          },
        },
      },

      MuiBreadcrumbs: {
        styleOverrides: {
          root: { fontSize: '0.8125rem' },
          separator: { marginInline: 6, color: t.disabled },
        },
      },

      MuiCircularProgress: {
        styleOverrides: { circle: { strokeLinecap: 'round' } },
      },

      MuiAutocomplete: {
        styleOverrides: {
          paper: {
            borderRadius: radii.control,
            border: `1px solid ${s.border}`,
            boxShadow: e.lg,
            marginTop: 6,
          },
          option: {
            borderRadius: radii.sm,
            margin: 4,
            fontSize: '0.875rem',
            minHeight: 36,
          },
        },
      },
    },
  });
}
