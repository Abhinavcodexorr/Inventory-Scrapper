import type { ReactNode } from 'react';
import { Box, type SxProps, type Theme } from '@mui/material';

interface Props {
  children: ReactNode;
  /** Stagger index — each step delays the entrance by 45ms. */
  index?: number;
  /** Translate distance in px before settling. */
  distance?: number;
  sx?: SxProps<Theme>;
}

/**
 * CSS-only entrance animation (opacity + transform, both GPU-composited, so no
 * layout shift). Staggering by index makes grids resolve in a readable order
 * instead of all snapping at once.
 */
export function AppearIn({ children, index = 0, distance = 8, sx }: Props) {
  return (
    <Box
      sx={[
        {
          '@keyframes appear-in': {
            from: { opacity: 0, transform: `translate3d(0, ${distance}px, 0)` },
            to: { opacity: 1, transform: 'translate3d(0, 0, 0)' },
          },
          opacity: 0,
          animation: 'appear-in 420ms cubic-bezier(0.16, 1, 0.3, 1) forwards',
          animationDelay: `${Math.min(index, 12) * 45}ms`,
          '@media (prefers-reduced-motion: reduce)': {
            animation: 'none',
            opacity: 1,
            transform: 'none',
          },
        },
        ...(Array.isArray(sx) ? sx : [sx]),
      ]}
    >
      {children}
    </Box>
  );
}
