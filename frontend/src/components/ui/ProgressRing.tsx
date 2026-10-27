import { useId } from 'react';
import { Box, Typography } from '@mui/material';
import { alpha, useTheme } from '@mui/material/styles';

interface Props {
  /** 0–100. */
  value: number;
  size?: number;
  thickness?: number;
  color?: string;
  label?: string;
  /** Hide the centred percentage. */
  hideValue?: boolean;
}

/** Circular percentage gauge with a gradient sweep and animated fill. */
export function ProgressRing({ value, size = 120, thickness = 10, color, label, hideValue = false }: Props) {
  const theme = useTheme();
  const gradientId = useId();
  const accent = color ?? theme.palette.primary.main;

  const clamped = Math.max(0, Math.min(100, value));
  const radius = (size - thickness) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference * (1 - clamped / 100);

  return (
    <Box sx={{ position: 'relative', width: size, height: size, flexShrink: 0 }}>
      <Box
        component="svg"
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        role="img"
        aria-label={`${label ?? 'Progress'}: ${clamped.toFixed(0)}%`}
        sx={{ transform: 'rotate(-90deg)' }}
      >
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor={accent} />
            <stop offset="100%" stopColor={alpha(accent, 0.6)} />
          </linearGradient>
        </defs>
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={alpha(theme.palette.text.primary, theme.palette.mode === 'light' ? 0.07 : 0.12)}
          strokeWidth={thickness}
        />
        <Box
          component="circle"
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={`url(#${gradientId})`}
          strokeWidth={thickness}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          sx={{
            transition: 'stroke-dashoffset 900ms cubic-bezier(0.16, 1, 0.3, 1)',
            '@media (prefers-reduced-motion: reduce)': { transition: 'none' },
          }}
        />
      </Box>

      {!hideValue && (
        <Box
          sx={{
            position: 'absolute',
            inset: 0,
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 0.25,
          }}
        >
          <Typography sx={{ fontSize: size * 0.22, fontWeight: 680, letterSpacing: '-0.03em', lineHeight: 1 }}>
            {clamped.toFixed(clamped % 1 === 0 ? 0 : 1)}
            <Box component="span" sx={{ fontSize: '0.5em', ml: 0.25, color: 'text.secondary' }}>
              %
            </Box>
          </Typography>
          {label && (
            <Typography variant="caption" color="text.secondary" sx={{ textAlign: 'center', px: 1 }}>
              {label}
            </Typography>
          )}
        </Box>
      )}
    </Box>
  );
}
