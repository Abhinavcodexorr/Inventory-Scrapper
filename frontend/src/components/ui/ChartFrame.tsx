import type { ReactNode } from 'react';
import { Box, Skeleton, type SxProps, type Theme } from '@mui/material';
import { alpha, useTheme } from '@mui/material/styles';
import { EmptyState, type EmptyArt } from './EmptyState';
import { useTokens } from '../../theme/useTokens';

/**
 * Shared visual language for every @mui/x-charts surface: hairline axes, dashed
 * grid, muted tick labels and a card-styled tooltip. Spread onto a chart's `sx`.
 */
export function useChartSx(): SxProps<Theme> {
  const theme = useTheme();
  const tk = useTokens();
  const light = theme.palette.mode === 'light';

  return {
    '& .MuiChartsAxis-line': { stroke: tk.surfaces.border, strokeWidth: 1 },
    '& .MuiChartsAxis-tick': { stroke: 'transparent' },
    '& .MuiChartsAxis-tickLabel': {
      fill: theme.palette.text.secondary,
      fontSize: 11,
      fontWeight: 500,
      letterSpacing: '0.01em',
    },
    '& .MuiChartsAxis-label': { fill: theme.palette.text.secondary, fontSize: 11, fontWeight: 600 },
    '& .MuiChartsGrid-line': {
      stroke: light ? alpha('#0F172A', 0.06) : alpha('#94A3B8', 0.12),
      strokeDasharray: '3 5',
    },
    '& .MuiChartsLegend-series text': { fill: `${theme.palette.text.secondary} !important`, fontSize: '12px !important' },
    '& .MuiChartsTooltip-root, & .MuiChartsTooltip-paper': {
      borderRadius: `${tk.radii.control}px`,
      border: `1px solid ${tk.surfaces.border}`,
      boxShadow: tk.elevation.lg,
      backgroundColor: tk.surfaces.raised,
    },
    '& .MuiAreaElement-root': { fillOpacity: 0.16 },
    '& .MuiMarkElement-root': { strokeWidth: 2, scale: '0.85' },
    '& .MuiBarElement-root': { rx: 6, ry: 6 },
  };
}

interface FrameProps {
  /** Rendered once data is available. */
  children: ReactNode;
  loading?: boolean;
  /** True when there is nothing to plot. */
  isEmpty?: boolean;
  height?: number;
  emptyTitle?: string;
  emptyDescription?: ReactNode;
  emptyArt?: EmptyArt;
  emptyAction?: ReactNode;
}

/** Fixed-height chart slot with matching skeleton and empty treatments. */
export function ChartFrame({
  children,
  loading = false,
  isEmpty = false,
  height = 280,
  emptyTitle = 'No data yet',
  emptyDescription,
  emptyArt = 'chart',
  emptyAction,
}: FrameProps) {
  if (loading) {
    return (
      <Box sx={{ height, display: 'flex', alignItems: 'flex-end', gap: 1.5, px: 1, pb: 3 }}>
        {[62, 38, 78, 52, 88, 44, 70, 58, 92, 48].map((h, i) => (
          <Skeleton
            key={i}
            variant="rounded"
            sx={{ flex: 1, height: `${h}%`, borderRadius: 1, animationDelay: `${i * 60}ms` }}
          />
        ))}
      </Box>
    );
  }

  if (isEmpty) {
    return (
      <Box sx={{ height, display: 'grid', placeItems: 'center' }}>
        <EmptyState dense art={emptyArt} title={emptyTitle} description={emptyDescription} action={emptyAction} />
      </Box>
    );
  }

  return <Box sx={{ height, width: '100%' }}>{children}</Box>;
}
