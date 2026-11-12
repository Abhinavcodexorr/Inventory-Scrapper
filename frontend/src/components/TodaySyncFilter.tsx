import { Box, ToggleButton, ToggleButtonGroup, Tooltip, Typography } from '@mui/material';
import { useTheme } from '@mui/material/styles';
import type { TodaySyncBucket } from '../api/dealers';
import { useTodaySyncCounts } from '../hooks/useDealers';

const OPTIONS: { value: TodaySyncBucket; label: string; hint: string }[] = [
  { value: 'running', label: 'In progress', hint: 'A scrape is running right now' },
  { value: 'pending', label: 'Waiting', hint: "Active dealerships that haven't been synced yet today" },
  { value: 'done', label: 'Done', hint: 'Synced today and it succeeded' },
  { value: 'failed', label: 'Failed', hint: 'Synced today but it failed or came back incomplete' },
];

interface Props {
  value: TodaySyncBucket | '';
  onChange: (next: TodaySyncBucket | '') => void;
}

/**
 * Today's sync at a glance: how many dealerships are in progress, waiting, done or
 * failed, each one a filter. Counts come from the same server conditions the list
 * filters on, refresh every few seconds, and "today" starts at local midnight.
 * Click the selected button again to clear it.
 */
export function TodaySyncFilter({ value, onChange }: Props) {
  const theme = useTheme();
  const counts = useTodaySyncCounts().data;
  const color: Record<TodaySyncBucket, string> = {
    running: theme.palette.info.main,
    pending: theme.palette.warning.main,
    done: theme.palette.success.main,
    failed: theme.palette.error.main,
  };

  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
      <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600, whiteSpace: 'nowrap' }}>
        Today&apos;s sync
      </Typography>
      <ToggleButtonGroup
        exclusive
        size="small"
        value={value || null}
        aria-label="Filter by today's sync"
        onChange={(_, next: TodaySyncBucket | null) => onChange(next ?? '')}
      >
        {OPTIONS.map(({ value: v, label, hint }) => {
          const n = counts?.[v];
          const live = v === 'running' && (n ?? 0) > 0;
          return (
            <Tooltip key={v} title={hint}>
              <ToggleButton value={v} sx={{ textTransform: 'none', px: 1.5, gap: 0.75 }}>
                <Box
                  component="span"
                  sx={{
                    width: 7,
                    height: 7,
                    borderRadius: '50%',
                    bgcolor: color[v],
                    flexShrink: 0,
                    // A live dot only while something is actually scraping.
                    ...(live && {
                      animation: 'todaySyncPulse 1.4s ease-in-out infinite',
                      '@keyframes todaySyncPulse': { '0%, 100%': { opacity: 1 }, '50%': { opacity: 0.25 } },
                      '@media (prefers-reduced-motion: reduce)': { animation: 'none' },
                    }),
                  }}
                />
                {label}
                <Typography
                  component="span"
                  variant="caption"
                  color="text.secondary"
                  sx={{ fontVariantNumeric: 'tabular-nums' }}
                >
                  {n === undefined ? '' : n.toLocaleString()}
                </Typography>
              </ToggleButton>
            </Tooltip>
          );
        })}
      </ToggleButtonGroup>
    </Box>
  );
}
