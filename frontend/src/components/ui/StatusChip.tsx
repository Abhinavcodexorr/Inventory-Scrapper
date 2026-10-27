import { Box, Typography } from '@mui/material';
import { alpha, useTheme } from '@mui/material/styles';

type Tone = 'success' | 'warning' | 'danger' | 'info' | 'neutral' | 'live';

/**
 * Maps every status string the API can return onto a visual tone.
 * Keys are lowercased before lookup, so Celery's SCREAMING_CASE job states and
 * the DB's snake_case run states both resolve here.
 */
const TONE_BY_STATUS: Record<string, Tone> = {
  // sync runs
  success: 'success',
  completed: 'success',
  incomplete: 'warning',
  cancelled: 'warning',
  canceled: 'warning',
  failed: 'danger',
  running: 'live',
  queued: 'info',
  pending: 'info',
  // celery job states
  started: 'live',
  failure: 'danger',
  retry: 'warning',
  revoked: 'neutral',
  // dealers
  active: 'success',
  inactive: 'neutral',
  // vehicles
  removed: 'neutral',
  sold: 'info',
  // discovery verdicts
  ready: 'success',
  needs_review: 'warning',
  unsupported: 'danger',
};

const LABEL_OVERRIDES: Record<string, string> = {
  started: 'Running',
  failure: 'Failed',
  success: 'Success',
  needs_review: 'Needs review',
};

interface Props {
  status: string | null | undefined;
  size?: 'small' | 'medium';
  /** Hide the leading dot (useful in very dense cells). */
  hideDot?: boolean;
}

/**
 * Soft tonal status pill with a leading dot. Running states pulse so a live
 * scrape is identifiable at a glance without reading the label.
 */
export function StatusChip({ status, size = 'small', hideDot = false }: Props) {
  const theme = useTheme();
  const light = theme.palette.mode === 'light';

  if (!status) {
    return (
      <Typography component="span" variant="body2" color="text.disabled" aria-label="No status">
        —
      </Typography>
    );
  }

  const key = String(status).toLowerCase();
  const tone = TONE_BY_STATUS[key] ?? 'neutral';
  const label = LABEL_OVERRIDES[key] ?? key.replace(/_/g, ' ');

  const color =
    tone === 'success'
      ? theme.palette.success.main
      : tone === 'warning'
        ? theme.palette.warning.main
        : tone === 'danger'
          ? theme.palette.error.main
          : tone === 'info'
            ? theme.palette.info.main
            : tone === 'live'
              ? theme.palette.primary.main
              : theme.palette.text.secondary;

  const dense = size === 'small';

  return (
    <Box
      component="span"
      sx={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 0.75,
        height: dense ? 24 : 30,
        px: dense ? 1 : 1.25,
        borderRadius: 999,
        maxWidth: '100%',
        bgcolor: alpha(color, light ? 0.1 : 0.16),
        color: tone === 'neutral' ? 'text.secondary' : color,
        boxShadow: `inset 0 0 0 1px ${alpha(color, light ? 0.16 : 0.24)}`,
        fontSize: dense ? '0.75rem' : '0.8125rem',
        fontWeight: 600,
        lineHeight: 1,
        textTransform: 'capitalize',
        whiteSpace: 'nowrap',
        '@keyframes status-pulse': {
          '0%': { boxShadow: `0 0 0 0 ${alpha(color, 0.5)}` },
          '70%': { boxShadow: `0 0 0 5px ${alpha(color, 0)}` },
          '100%': { boxShadow: `0 0 0 0 ${alpha(color, 0)}` },
        },
      }}
    >
      {!hideDot && (
        <Box
          component="span"
          sx={{
            width: 6,
            height: 6,
            borderRadius: '50%',
            bgcolor: 'currentColor',
            flexShrink: 0,
            ...(tone === 'live'
              ? {
                  animation: 'status-pulse 1.8s ease-out infinite',
                  '@media (prefers-reduced-motion: reduce)': { animation: 'none' },
                }
              : null),
          }}
        />
      )}
      <Box component="span" sx={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>
        {label}
      </Box>
    </Box>
  );
}
