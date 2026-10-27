import { forwardRef, type ElementType, type ReactNode } from 'react';
import { Box, type BoxProps } from '@mui/material';
import { useTokens } from '../../theme/useTokens';

export interface SurfaceProps extends Omit<BoxProps, 'children'> {
  children?: ReactNode;
  /** `card` sits on the canvas, `sunken` recedes into it, `raised` floats above. */
  tone?: 'card' | 'sunken' | 'raised';
  /** Lift + shadow on hover. Set for anything clickable. */
  interactive?: boolean;
  /** Override the default 16px card radius. */
  radius?: number;
  /** Render as another element — `button` when the whole surface is clickable. */
  component?: ElementType;
  /** Needed when `component="button"`, which React requires an explicit type on. */
  type?: 'button' | 'submit' | 'reset';
}

/**
 * The base panel. Everything visually "on a card" in this app goes through here
 * so radius, border and elevation stay identical across pages.
 */
export const Surface = forwardRef<HTMLDivElement, SurfaceProps>(function Surface(
  { children, tone = 'card', interactive = false, radius, sx, component = 'div', ...rest },
  ref,
) {
  const tk = useTokens();

  const background =
    tone === 'sunken' ? tk.surfaces.sunken : tone === 'raised' ? tk.surfaces.raised : tk.surfaces.surface;

  return (
    <Box
      ref={ref}
      component={component}
      sx={[
        {
          position: 'relative',
          borderRadius: `${radius ?? tk.radii.card}px`,
          border: `1px solid ${tk.surfaces.cardBorder}`,
          backgroundColor: background,
          boxShadow: tone === 'raised' ? tk.elevation.lg : tone === 'sunken' ? 'none' : tk.elevation.sm,
          ...(interactive
            ? {
                cursor: 'pointer',
                transition: `transform ${tk.motion.base} ${tk.motion.ease}, box-shadow ${tk.motion.base} ${tk.motion.ease}, border-color ${tk.motion.base} ${tk.motion.ease}`,
                willChange: 'transform',
                '&:hover': {
                  transform: 'translate3d(0, -2px, 0)',
                  boxShadow: tk.elevation.lg,
                  borderColor: tk.surfaces.border,
                },
                '&:active': { transform: 'translate3d(0, 0, 0)', boxShadow: tk.elevation.sm },
                '&:focus-visible': { outline: 'none', boxShadow: tk.focusRing },
                '@media (prefers-reduced-motion: reduce)': {
                  '&:hover': { transform: 'none' },
                },
              }
            : null),
        },
        ...(Array.isArray(sx) ? sx : [sx]),
      ]}
      {...rest}
    >
      {children}
    </Box>
  );
});
