import { Box } from '@mui/material';
import { useTheme } from '@mui/material/styles';
import { useId } from 'react';

/**
 * Product mark: a stylised speed/inventory glyph. Drawn inline so it inherits
 * the theme's primary ramp instead of shipping a second set of brand assets.
 */
export function Logo({ size = 32 }: { size?: number }) {
  const theme = useTheme();
  const gradientId = useId();
  const primary = theme.palette.primary.main;
  const light = theme.palette.primary.light;

  return (
    <Box
      component="svg"
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      aria-hidden="true"
      sx={{ flexShrink: 0, display: 'block' }}
    >
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor={light} />
          <stop offset="100%" stopColor={primary} />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="9" fill={`url(#${gradientId})`} />
      <path
        d="M8 20.5c0-.6.4-1 1-1h4.2c.5 0 .9.4.9 1s-.4 1-.9 1H9c-.6 0-1-.5-1-1Z"
        fill="#fff"
        fillOpacity="0.55"
      />
      <path
        d="M8 16c0-.6.4-1 1-1h9.5c.6 0 1 .4 1 1s-.4 1-1 1H9c-.6 0-1-.4-1-1Z"
        fill="#fff"
        fillOpacity="0.8"
      />
      <path d="M8 11.5c0-.6.4-1 1-1h14c.6 0 1 .4 1 1s-.4 1-1 1H9c-.6 0-1-.4-1-1Z" fill="#fff" />
      <circle cx="21.5" cy="20.5" r="3.5" fill="#fff" />
      <circle cx="21.5" cy="20.5" r="1.4" fill={primary} />
    </Box>
  );
}
