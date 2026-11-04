import { Box } from '@mui/material';
import { useTheme } from '@mui/material/styles';
import { useId } from 'react';

/**
 * Product mark: a car on a rounded tile. Drawn inline so it inherits
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
        d="M5.5 20.5v-3.3c0-.7.4-1.3 1-1.6l2-1 1.8-3.8c.4-.9 1.3-1.5 2.3-1.5h6.8c.9 0 1.8.5 2.2 1.3l2.1 3.9 1.7.6c.8.3 1.2 1 1.2 1.8v3.6c0 .6-.4 1-1 1h-19c-.6 0-1.1-.4-1.1-1Z"
        fill="#fff"
      />
      <path
        d="M11.4 14.2l1.3-2.8c.1-.2.3-.4.6-.4h2.7v3.2h-4.6Zm6.6 0V11h1.8c.2 0 .4.1.5.3l1.6 2.9H18Z"
        fill={primary}
      />
      <circle cx="10.8" cy="21.8" r="2.9" fill="#fff" stroke={primary} strokeWidth="1.4" />
      <circle cx="21.2" cy="21.8" r="2.9" fill="#fff" stroke={primary} strokeWidth="1.4" />
    </Box>
  );
}
