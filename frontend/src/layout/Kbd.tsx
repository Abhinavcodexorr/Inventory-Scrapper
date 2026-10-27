import type { ReactNode } from 'react';
import { Box } from '@mui/material';
import { useTokens } from '../theme/useTokens';

/** Keycap chip used in shortcut hints. */
export function Kbd({ children }: { children: ReactNode }) {
  const tk = useTokens();

  return (
    <Box
      component="kbd"
      sx={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        minWidth: 20,
        height: 20,
        px: 0.75,
        borderRadius: `${tk.radii.xs}px`,
        border: `1px solid ${tk.surfaces.border}`,
        borderBottomWidth: 2,
        bgcolor: tk.surfaces.sunken,
        color: 'text.secondary',
        fontFamily: 'inherit',
        fontSize: '0.6875rem',
        fontWeight: 600,
        lineHeight: 1,
      }}
    >
      {children}
    </Box>
  );
}
