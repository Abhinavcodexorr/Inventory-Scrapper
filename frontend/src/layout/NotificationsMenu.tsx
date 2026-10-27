import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Badge, Box, Divider, IconButton, Menu, Tooltip, Typography } from '@mui/material';
import { alpha, useTheme } from '@mui/material/styles';
import NotificationsNoneRoundedIcon from '@mui/icons-material/NotificationsNoneRounded';
import ErrorOutlineRoundedIcon from '@mui/icons-material/ErrorOutlineRounded';
import ScheduleRoundedIcon from '@mui/icons-material/ScheduleRounded';
import TrendingDownRoundedIcon from '@mui/icons-material/TrendingDownRounded';
import CheckCircleOutlineRoundedIcon from '@mui/icons-material/CheckCircleOutlineRounded';
import { useDealerStats } from '../hooks/useDealers';
import { useTokens } from '../theme/useTokens';
import { formatRelative } from '../utils/format';

type Severity = 'error' | 'warning' | 'info';

interface Alert {
  id: string;
  severity: Severity;
  title: string;
  detail: string;
  timestamp?: string | null;
  to: string;
}

/**
 * Notifications derived from live dealer telemetry — there is no notifications
 * endpoint, so these are computed from the same stats the dashboard reads.
 */
export function NotificationsMenu() {
  const theme = useTheme();
  const tk = useTokens();
  const navigate = useNavigate();
  const statsQ = useDealerStats();
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);

  const alerts = useMemo<Alert[]>(() => {
    const out: Alert[] = [];
    for (const s of statsQ.data ?? []) {
      if (s.sync_status === 'failed') {
        out.push({
          id: `failed-${s.dealer_id}`,
          severity: 'error',
          title: `${s.dealer_name} failed to sync`,
          detail: 'The last scrape run ended with an error.',
          timestamp: s.last_sync_at,
          to: `/dealers/${s.dealer_id}`,
        });
      } else if (!s.last_sync_at) {
        out.push({
          id: `never-${s.dealer_id}`,
          severity: 'warning',
          title: `${s.dealer_name} has never synced`,
          detail: 'Run a scrape to populate its inventory.',
          to: `/dealers/${s.dealer_id}`,
        });
      }

      // A double-digit percentage drop usually means a broken selector rather
      // than genuine sales, so it is worth surfacing.
      const drop = s.total_yesterday - s.total_today;
      if (s.total_yesterday > 20 && drop / s.total_yesterday > 0.2) {
        out.push({
          id: `drop-${s.dealer_id}`,
          severity: 'warning',
          title: `${s.dealer_name} inventory dropped ${Math.round((drop / s.total_yesterday) * 100)}%`,
          detail: `${s.total_yesterday.toLocaleString()} → ${s.total_today.toLocaleString()} vehicles since yesterday.`,
          timestamp: s.last_sync_at,
          to: `/dealers/${s.dealer_id}`,
        });
      }
    }
    const rank: Record<Severity, number> = { error: 0, warning: 1, info: 2 };
    return out.sort((a, b) => rank[a.severity] - rank[b.severity]).slice(0, 12);
  }, [statsQ.data]);

  const colorFor = (severity: Severity) =>
    severity === 'error'
      ? theme.palette.error.main
      : severity === 'warning'
        ? theme.palette.warning.main
        : theme.palette.info.main;

  const iconFor = (severity: Severity) =>
    severity === 'error' ? (
      <ErrorOutlineRoundedIcon sx={{ fontSize: 17 }} />
    ) : severity === 'warning' ? (
      <TrendingDownRoundedIcon sx={{ fontSize: 17 }} />
    ) : (
      <ScheduleRoundedIcon sx={{ fontSize: 17 }} />
    );

  return (
    <>
      <Tooltip title="Notifications">
        <IconButton onClick={(e) => setAnchor(e.currentTarget)} aria-label={`Notifications (${alerts.length})`}>
          <Badge
            badgeContent={alerts.length}
            color="error"
            max={9}
            slotProps={{ badge: { style: { fontSize: '0.625rem', minWidth: 16, height: 16 } } }}
          >
            <NotificationsNoneRoundedIcon sx={{ fontSize: 20 }} />
          </Badge>
        </IconButton>
      </Tooltip>

      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
        transformOrigin={{ vertical: 'top', horizontal: 'right' }}
        slotProps={{ paper: { sx: { width: 380, maxWidth: 'calc(100vw - 32px)', p: 0 } } }}
      >
        <Box sx={{ px: 2, py: 1.5, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <Typography variant="subtitle2" sx={{ fontWeight: 660 }}>
            Notifications
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {alerts.length === 0 ? 'All clear' : `${alerts.length} needing attention`}
          </Typography>
        </Box>
        <Divider />

        {alerts.length === 0 ? (
          <Box sx={{ px: 3, py: 5, textAlign: 'center' }}>
            <CheckCircleOutlineRoundedIcon sx={{ fontSize: 32, color: 'success.main', mb: 1 }} />
            <Typography variant="body2" sx={{ fontWeight: 560 }}>
              Everything is healthy
            </Typography>
            <Typography variant="caption" color="text.secondary">
              No failed syncs or unusual inventory movement.
            </Typography>
          </Box>
        ) : (
          <Box sx={{ maxHeight: 380, overflowY: 'auto', p: 1 }}>
            {alerts.map((alert) => {
              const color = colorFor(alert.severity);
              return (
                <Box
                  key={alert.id}
                  onClick={() => {
                    navigate(alert.to);
                    setAnchor(null);
                  }}
                  sx={{
                    display: 'flex',
                    gap: 1.5,
                    p: 1.25,
                    borderRadius: `${tk.radii.sm}px`,
                    cursor: 'pointer',
                    transition: `background-color ${tk.motion.fast} ${tk.motion.ease}`,
                    '&:hover': { bgcolor: tk.surfaces.hover },
                  }}
                >
                  <Box
                    sx={{
                      width: 28,
                      height: 28,
                      flexShrink: 0,
                      borderRadius: '50%',
                      display: 'grid',
                      placeItems: 'center',
                      color,
                      bgcolor: alpha(color, theme.palette.mode === 'light' ? 0.1 : 0.18),
                    }}
                  >
                    {iconFor(alert.severity)}
                  </Box>
                  <Box sx={{ minWidth: 0 }}>
                    <Typography variant="body2" sx={{ fontWeight: 560 }}>
                      {alert.title}
                    </Typography>
                    <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                      {alert.detail}
                    </Typography>
                    {alert.timestamp && (
                      <Typography variant="caption" color="text.disabled">
                        {formatRelative(alert.timestamp)}
                      </Typography>
                    )}
                  </Box>
                </Box>
              );
            })}
          </Box>
        )}
      </Menu>
    </>
  );
}
