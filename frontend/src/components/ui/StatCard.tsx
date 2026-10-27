import type { ReactNode } from 'react';
import { Box, Skeleton, Typography } from '@mui/material';
import { alpha, useTheme } from '@mui/material/styles';
import { Surface } from './Surface';
import { SoftIconTile } from './SoftIconTile';
import { CountUp } from './CountUp';
import { TrendPill } from './TrendPill';
import { Sparkline } from './Sparkline';
import { useTokens } from '../../theme/useTokens';

export interface StatCardProps {
  label: string;
  value: number;
  icon: ReactNode;
  color: string;
  /** Signed change against the comparison period. */
  delta?: number;
  deltaSuffix?: string;
  deltaCaption?: string;
  /** Down is good (failures, durations). */
  invertDelta?: boolean;
  /** Trailing series drawn along the card floor. */
  series?: readonly number[];
  decimals?: number;
  suffix?: string;
  prefix?: string;
  loading?: boolean;
  onClick?: () => void;
  /** Emphasises the card when the metric needs attention (e.g. failures > 0). */
  alert?: boolean;
  /** Promotes this tile as the single headline metric in a KPI row. */
  emphasis?: boolean;
  /** Tighter padding and smaller type — for dense grids like the dashboard KPI row. */
  dense?: boolean;
}

/**
 * KPI tile: icon, animated value, trend delta and a trailing sparkline.
 * Hover lifts the card and brightens its accent wash.
 */
export function StatCard({
  label,
  value,
  icon,
  color,
  delta,
  deltaSuffix,
  deltaCaption,
  invertDelta,
  series,
  decimals = 0,
  suffix,
  prefix,
  loading = false,
  onClick,
  alert = false,
  emphasis = false,
  dense = false,
}: StatCardProps) {
  const tk = useTokens();
  const theme = useTheme();
  const accent = alert ? theme.palette.error.main : color;
  const pad = dense ? 2 : 2.5;
  const iconSize = dense ? (emphasis ? 38 : 34) : emphasis ? 44 : 40;
  const sparklineHeight = dense ? 36 : 44;

  if (loading) {
    return (
      <Surface sx={{ p: pad, minHeight: dense ? 140 : 168, height: '100%' }}>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: dense ? 1.5 : 2 }}>
          <Skeleton width={96} height={14} />
          <Skeleton variant="rounded" width={iconSize} height={iconSize} />
        </Box>
        <Skeleton width={110} height={dense ? 32 : 40} sx={{ mb: 1 }} />
        <Skeleton width={80} height={20} />
      </Surface>
    );
  }

  return (
    <Surface
      interactive={Boolean(onClick)}
      component={onClick ? 'button' : 'div'}
      onClick={onClick}
      {...(onClick ? { type: 'button', 'aria-label': `${label}: ${value}` } : {})}
      sx={{
        p: pad,
        pb: series?.length ? 0 : pad,
        minHeight: dense ? 140 : 168,
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        textAlign: 'left',
        font: 'inherit',
        width: '100%',
        overflow: 'hidden',
        // Accent wash bleeding from the top-right corner, intensified on hover.
        '&::before': {
          content: '""',
          position: 'absolute',
          inset: 0,
          borderRadius: 'inherit',
          pointerEvents: 'none',
          background: `radial-gradient(120% 90% at 100% 0%, ${alpha(accent, theme.palette.mode === 'light' ? (emphasis ? 0.1 : 0.07) : emphasis ? 0.16 : 0.12)} 0%, transparent 62%)`,
          opacity: 0.85,
          transition: `opacity ${tk.motion.base} ${tk.motion.ease}`,
        },
        '&:hover::before': { opacity: 1 },
      }}
    >
      <Box sx={{ position: 'relative', display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 1.5 }}>
        <Typography
          variant="overline"
          sx={{ color: 'text.secondary', display: 'block', pt: 0.5, letterSpacing: '0.05em' }}
        >
          {label}
        </Typography>
        <SoftIconTile color={accent} variant={emphasis ? 'solid' : 'soft'} size={iconSize}>
          {icon}
        </SoftIconTile>
      </Box>

      <Typography
        component="div"
        sx={{
          position: 'relative',
          mt: 1,
          fontSize: dense
            ? emphasis
              ? 'clamp(1.625rem, 2.4vw, 2rem)'
              : 'clamp(1.375rem, 1.8vw, 1.625rem)'
            : emphasis
              ? 'clamp(2rem, 3vw, 2.5rem)'
              : 'clamp(1.75rem, 2.4vw, 2.125rem)',
          fontWeight: 680,
          letterSpacing: '-0.035em',
          lineHeight: 1.1,
          color: alert && value > 0 ? 'error.main' : 'text.primary',
        }}
      >
        <CountUp value={value} decimals={decimals} suffix={suffix} prefix={prefix} />
      </Typography>

      <Box sx={{ position: 'relative', mt: 1, minHeight: dense ? 20 : 24, display: 'flex', alignItems: 'center' }}>
        {delta !== undefined ? (
          <TrendPill value={delta} suffix={deltaSuffix} invert={invertDelta} caption={deltaCaption} />
        ) : (
          deltaCaption && (
            <Typography variant="caption" color="text.secondary" noWrap>
              {deltaCaption}
            </Typography>
          )
        )}
      </Box>

      {series && series.length > 1 && (
        <Box sx={{ position: 'relative', mt: 'auto', mx: -pad, pt: dense ? 1 : 1.5 }}>
          <Sparkline data={series} color={accent} height={sparklineHeight} />
        </Box>
      )}
    </Surface>
  );
}
