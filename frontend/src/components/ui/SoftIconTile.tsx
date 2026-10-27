import type { ReactNode } from 'react';
import { Box } from '@mui/material';
import { alpha, useTheme } from '@mui/material/styles';
import { radii } from '../../theme/tokens';

interface Props {
  children: ReactNode;
  /** Any CSS colour — the tile derives its wash and ring from it. */
  color: string;
  size?: number;
  /** Solid gradient fill instead of a tint. Use for hero/primary emphasis. */
  variant?: 'soft' | 'solid';
}

/** Tinted rounded tile behind an icon — the recurring accent shape of the UI. */
export function SoftIconTile({ children, color, size = 40, variant = 'soft' }: Props) {
  const theme = useTheme();
  const light = theme.palette.mode === 'light';

  return (
    <Box
      aria-hidden="true"
      sx={{
        width: size,
        height: size,
        flexShrink: 0,
        borderRadius: `${size >= 40 ? radii.control : radii.sm}px`,
        display: 'grid',
        placeItems: 'center',
        color: variant === 'solid' ? '#fff' : color,
        background:
          variant === 'solid'
            ? `linear-gradient(140deg, ${color} 0%, ${alpha(color, 0.78)} 100%)`
            : alpha(color, light ? 0.1 : 0.16),
        boxShadow:
          variant === 'solid'
            ? `0 4px 12px -4px ${alpha(color, 0.6)}`
            : `inset 0 0 0 1px ${alpha(color, light ? 0.14 : 0.2)}`,
        '& > svg': { fontSize: size * 0.5 },
      }}
    >
      {children}
    </Box>
  );
}
