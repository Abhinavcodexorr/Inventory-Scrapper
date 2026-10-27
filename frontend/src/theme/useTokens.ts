import { useMemo } from 'react';
import { useTheme } from '@mui/material/styles';
import { tokensFor, type Tokens } from './tokens';

/** Design tokens for the currently active colour mode. */
export function useTokens(): Tokens {
  const theme = useTheme();
  const mode = theme.palette.mode;
  return useMemo(() => tokensFor(mode), [mode]);
}
