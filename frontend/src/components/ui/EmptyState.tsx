import type { ReactNode } from 'react';
import { Box, Typography } from '@mui/material';
import { alpha, useTheme } from '@mui/material/styles';

export type EmptyArt = 'dealers' | 'vehicles' | 'jobs' | 'chart' | 'search' | 'error' | 'filter';

interface Props {
  art?: EmptyArt;
  title: string;
  description?: ReactNode;
  /** Primary call to action. */
  action?: ReactNode;
  /** Lower-emphasis alternative next to the CTA. */
  secondaryAction?: ReactNode;
  /** Shrinks padding for use inside a table body. */
  dense?: boolean;
}

/**
 * Illustrated empty state. Every "nothing here" moment in the app renders one of
 * these — a bare sentence never appears on its own.
 */
export function EmptyState({ art = 'chart', title, description, action, secondaryAction, dense = false }: Props) {
  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        textAlign: 'center',
        px: 3,
        py: dense ? 5 : 8,
        gap: 0.5,
        width: '100%',
      }}
    >
      <Illustration art={art} />
      <Typography variant="h5" sx={{ mt: 3 }}>
        {title}
      </Typography>
      {description && (
        <Typography variant="body2" color="text.secondary" sx={{ maxWidth: 420, mt: 0.5 }}>
          {description}
        </Typography>
      )}
      {(action || secondaryAction) && (
        <Box sx={{ display: 'flex', gap: 1.5, mt: 3, flexWrap: 'wrap', justifyContent: 'center' }}>
          {action}
          {secondaryAction}
        </Box>
      )}
    </Box>
  );
}

/** Geometric line-art built from the theme palette so it recolours with the mode. */
function Illustration({ art }: { art: EmptyArt }) {
  const theme = useTheme();
  const accent = theme.palette.primary.main;
  const muted = theme.palette.mode === 'light' ? '#CBD5E1' : 'rgba(148,163,184,0.42)';
  const wash = alpha(accent, theme.palette.mode === 'light' ? 0.08 : 0.14);

  const common = {
    width: 132,
    height: 104,
    viewBox: '0 0 132 104',
    fill: 'none',
    'aria-hidden': true as const,
    focusable: 'false' as const,
  };

  const stroke = { stroke: muted, strokeWidth: 2, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const };
  const strokeAccent = { ...stroke, stroke: accent };

  return (
    <Box
      sx={{
        display: 'grid',
        placeItems: 'center',
        width: 132,
        height: 104,
        borderRadius: 3,
        background: `radial-gradient(60% 60% at 50% 45%, ${wash} 0%, transparent 72%)`,
      }}
    >
      {art === 'dealers' && (
        <svg {...common}>
          <path d="M30 44h72v34a4 4 0 0 1-4 4H34a4 4 0 0 1-4-4V44Z" fill={wash} {...stroke} />
          <path d="M26 44 34 26h64l8 18" {...stroke} />
          <path d="M26 44a8 8 0 0 0 16 0 8 8 0 0 0 16 0 8 8 0 0 0 16 0 8 8 0 0 0 16 0 8 8 0 0 0 16 0" {...strokeAccent} />
          <path d="M56 82V62h20v20" {...strokeAccent} />
        </svg>
      )}
      {art === 'vehicles' && (
        <svg {...common}>
          <path d="M22 66v-9l8-3 8-14a8 8 0 0 1 7-4h30a8 8 0 0 1 7 4l8 14 8 3v9a4 4 0 0 1-4 4H26a4 4 0 0 1-4-4Z" fill={wash} {...stroke} />
          <path d="M38 40h56" {...stroke} />
          <circle cx="42" cy="70" r="7" fill={theme.palette.background.paper} {...strokeAccent} />
          <circle cx="90" cy="70" r="7" fill={theme.palette.background.paper} {...strokeAccent} />
        </svg>
      )}
      {art === 'jobs' && (
        <svg {...common}>
          <rect x="20" y="24" width="92" height="14" rx="7" fill={wash} {...stroke} />
          <rect x="20" y="45" width="92" height="14" rx="7" {...stroke} />
          <rect x="20" y="66" width="92" height="14" rx="7" {...stroke} />
          <rect x="20" y="24" width="46" height="14" rx="7" fill={alpha(accent, 0.18)} {...strokeAccent} />
          <circle cx="98" cy="52" r="3" fill={muted} />
        </svg>
      )}
      {art === 'chart' && (
        <svg {...common}>
          <path d="M22 80h88" {...stroke} strokeDasharray="4 6" />
          <rect x="30" y="56" width="14" height="24" rx="4" fill={wash} {...stroke} />
          <rect x="52" y="42" width="14" height="38" rx="4" fill={wash} {...stroke} />
          <rect x="74" y="30" width="14" height="50" rx="4" fill={alpha(accent, 0.18)} {...strokeAccent} />
          <rect x="96" y="50" width="14" height="30" rx="4" fill={wash} {...stroke} />
        </svg>
      )}
      {art === 'search' && (
        <svg {...common}>
          <rect x="24" y="24" width="60" height="10" rx="5" {...stroke} />
          <rect x="24" y="44" width="42" height="10" rx="5" {...stroke} />
          <rect x="24" y="64" width="52" height="10" rx="5" {...stroke} />
          <circle cx="88" cy="58" r="18" fill={theme.palette.background.paper} {...strokeAccent} />
          <path d="m101 71 9 9" {...strokeAccent} />
        </svg>
      )}
      {art === 'filter' && (
        <svg {...common}>
          <path d="M28 28h76L76 60v22l-20 8V60L28 28Z" fill={wash} {...stroke} />
          <path d="M56 60 28 28h76" {...strokeAccent} />
        </svg>
      )}
      {art === 'error' && (
        <svg {...common}>
          <path
            d="M62.3 24.6 24.9 74.2A4 4 0 0 0 28.1 80.6h74.8a4 4 0 0 0 3.2-6.4L68.7 24.6a4 4 0 0 0-6.4 0Z"
            fill={alpha(theme.palette.error.main, 0.12)}
            stroke={theme.palette.error.main}
            strokeWidth={2}
            strokeLinejoin="round"
          />
          <path d="M65.5 44v16" stroke={theme.palette.error.main} strokeWidth={2.5} strokeLinecap="round" />
          <circle cx="65.5" cy="69" r="2.5" fill={theme.palette.error.main} />
        </svg>
      )}
    </Box>
  );
}
