import { Box, Typography } from '@mui/material';
import { alpha, useTheme } from '@mui/material/styles';
import ArrowUpwardRoundedIcon from '@mui/icons-material/ArrowUpwardRounded';
import ArrowDownwardRoundedIcon from '@mui/icons-material/ArrowDownwardRounded';
import RemoveRoundedIcon from '@mui/icons-material/RemoveRounded';

interface Props {
  /** Signed change. 0 renders a neutral pill. */
  value: number;
  /** Rendered after the number, e.g. `%`. */
  suffix?: string;
  /** When true a negative change is the good outcome (failures, sync time). */
  invert?: boolean;
  /** Short context shown next to the pill, e.g. "vs yesterday". */
  caption?: string;
  size?: 'small' | 'medium';
}

/** Compact signed-delta indicator with semantic colouring. */
export function TrendPill({ value, suffix = '', invert = false, caption, size = 'small' }: Props) {
  const theme = useTheme();
  const neutral = value === 0;
  const positive = invert ? value < 0 : value > 0;

  const color = neutral
    ? theme.palette.text.secondary
    : positive
      ? theme.palette.success.main
      : theme.palette.error.main;

  const Icon = neutral ? RemoveRoundedIcon : value > 0 ? ArrowUpwardRoundedIcon : ArrowDownwardRoundedIcon;
  const dense = size === 'small';

  return (
    <Box sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.75, minWidth: 0 }}>
      <Box
        sx={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 0.25,
          px: dense ? 0.75 : 1,
          height: dense ? 22 : 26,
          borderRadius: 999,
          bgcolor: alpha(color, theme.palette.mode === 'light' ? 0.1 : 0.18),
          color,
          fontWeight: 620,
          fontSize: dense ? '0.75rem' : '0.8125rem',
          lineHeight: 1,
          flexShrink: 0,
        }}
      >
        <Icon sx={{ fontSize: dense ? 13 : 15 }} />
        {value > 0 ? '+' : ''}
        {Number.isInteger(value) ? value : value.toFixed(1)}
        {suffix}
      </Box>
      {caption && (
        <Typography variant="caption" color="text.secondary" noWrap>
          {caption}
        </Typography>
      )}
    </Box>
  );
}
