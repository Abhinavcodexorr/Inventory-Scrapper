import { Fragment, useMemo } from 'react';
import { Link as RouterLink, useLocation } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import {
  Box,
  Button,
  CircularProgress,
  IconButton,
  Tooltip,
  Typography,
  useMediaQuery,
} from '@mui/material';
import { alpha, useTheme } from '@mui/material/styles';
import MenuRoundedIcon from '@mui/icons-material/MenuRounded';
import SearchRoundedIcon from '@mui/icons-material/SearchRounded';
import BoltRoundedIcon from '@mui/icons-material/BoltRounded';
import DarkModeRoundedIcon from '@mui/icons-material/DarkModeRounded';
import LightModeRoundedIcon from '@mui/icons-material/LightModeRounded';
import ChevronRightRoundedIcon from '@mui/icons-material/ChevronRightRounded';
import { NotificationsMenu } from './NotificationsMenu';
import { Kbd } from './Kbd';
import { ROUTE_LABELS } from './navConfig';
import { useDashboardSummary } from '../hooks/useDealers';
import { useTokens } from '../theme/useTokens';
import { useAppSettings } from '../theme/settings';
import { formatRelative } from '../utils/format';
import type { Dealer, Vehicle } from '../types';

interface Props {
  onOpenNav: () => void;
  onOpenSearch: () => void;
  onRunScraper: () => void;
  runningScraper: boolean;
}

export function Topbar({ onOpenNav, onOpenSearch, onRunScraper, runningScraper }: Props) {
  const tk = useTokens();
  const theme = useTheme();
  const location = useLocation();
  const qc = useQueryClient();
  const { mode, toggleTheme } = useAppSettings();
  const isDesktop = useMediaQuery(theme.breakpoints.up('lg'));
  const isMobile = useMediaQuery(theme.breakpoints.down('md'));

  const summaryQ = useDashboardSummary();

  /**
   * Breadcrumb trail from the URL. Numeric segments resolve to a real entity
   * name out of the query cache when one is already loaded — never by issuing a
   * fetch just to label a crumb.
   */
  const crumbs = useMemo(() => {
    const segments = location.pathname.split('/').filter(Boolean);
    const trail: { label: string; to: string }[] = [{ label: 'Dashboard', to: '/' }];

    segments.forEach((segment, i) => {
      const to = `/${segments.slice(0, i + 1).join('/')}`;
      if (/^\d+$/.test(segment)) {
        const id = Number(segment);
        const parent = segments[i - 1];
        let label = `#${segment}`;
        if (parent === 'dealers') {
          const cached =
            qc.getQueryData<Dealer>(['dealer', id]) ??
            qc.getQueryData<Dealer[]>(['dealers'])?.find((d) => d.id === id);
          if (cached) label = cached.name;
        } else if (parent === 'vehicles') {
          const cached = qc.getQueryData<Vehicle>(['vehicle', id]);
          if (cached) label = [cached.year, cached.make, cached.model].filter(Boolean).join(' ') || label;
        }
        trail.push({ label, to });
      } else {
        trail.push({ label: ROUTE_LABELS[segment] ?? segment.replace(/-/g, ' '), to });
      }
    });

    return trail;
  }, [location.pathname, qc]);

  const lastSync = summaryQ.data?.last_sync_at ?? null;
  const failed = summaryQ.data?.dealers_failed_last_run ?? 0;
  const syncTone = runningScraper
    ? theme.palette.primary.main
    : failed > 0
      ? theme.palette.error.main
      : lastSync
        ? theme.palette.success.main
        : theme.palette.text.disabled;

  return (
    <Box
      component="header"
      sx={{
        position: 'sticky',
        top: 0,
        zIndex: (t) => t.zIndex.appBar,
        height: tk.layout.topbarHeight,
        display: 'flex',
        alignItems: 'center',
        gap: 1.5,
        px: { xs: 2, md: 3 },
        borderBottom: `1px solid ${tk.surfaces.border}`,
        // Translucent so content scrolling underneath stays perceptible.
        backgroundColor: alpha(tk.surfaces.surface, 0.82),
        backdropFilter: 'blur(16px) saturate(160%)',
      }}
    >
      {isMobile && (
        <IconButton onClick={onOpenNav} aria-label="Open navigation" edge="start">
          <MenuRoundedIcon />
        </IconButton>
      )}

      {/* ── Breadcrumbs ────────────────────────────────────────────────── */}
      <Box
        component="nav"
        aria-label="Breadcrumb"
        sx={{ display: 'flex', alignItems: 'center', minWidth: 0, flex: 1, overflow: 'hidden' }}
      >
        {crumbs.map((crumb, i) => {
          const isLast = i === crumbs.length - 1;
          // On narrow screens only the current page is legible anyway.
          if (isMobile && !isLast) return null;
          return (
            <Fragment key={crumb.to}>
              {i > 0 && !isMobile && (
                <ChevronRightRoundedIcon sx={{ fontSize: 16, color: 'text.disabled', mx: 0.25, flexShrink: 0 }} />
              )}
              {isLast ? (
                <Typography variant="subtitle2" noWrap sx={{ fontWeight: 620, color: 'text.primary' }}>
                  {crumb.label}
                </Typography>
              ) : (
                <Typography
                  component={RouterLink}
                  to={crumb.to}
                  variant="body2"
                  noWrap
                  sx={{
                    color: 'text.secondary',
                    textDecoration: 'none',
                    borderRadius: 1,
                    px: 0.25,
                    '&:hover': { color: 'text.primary' },
                    '&:focus-visible': { outline: 'none', boxShadow: tk.focusRing },
                  }}
                >
                  {crumb.label}
                </Typography>
              )}
            </Fragment>
          );
        })}
      </Box>

      {/* ── Global search ──────────────────────────────────────────────── */}
      {isDesktop ? (
        <Box
          component="button"
          type="button"
          onClick={onOpenSearch}
          aria-label="Open global search"
          sx={{
            display: 'flex',
            alignItems: 'center',
            gap: 1,
            width: 260,
            height: 36,
            px: 1.25,
            border: `1px solid ${tk.surfaces.border}`,
            borderRadius: `${tk.radii.control}px`,
            bgcolor: tk.surfaces.sunken,
            color: theme.palette.text.disabled,
            cursor: 'pointer',
            font: 'inherit',
            fontSize: '0.8438rem',
            transition: `border-color ${tk.motion.fast} ${tk.motion.ease}, background-color ${tk.motion.fast} ${tk.motion.ease}`,
            '&:hover': { borderColor: tk.surfaces.borderStrong, bgcolor: tk.surfaces.surface },
            '&:focus-visible': { outline: 'none', boxShadow: tk.focusRing },
          }}
        >
          <SearchRoundedIcon sx={{ fontSize: 17 }} />
          <Box component="span" sx={{ flex: 1, textAlign: 'left' }}>
            Search…
          </Box>
          <Kbd>⌘K</Kbd>
        </Box>
      ) : (
        <Tooltip title="Search">
          <IconButton onClick={onOpenSearch} aria-label="Open global search">
            <SearchRoundedIcon sx={{ fontSize: 20 }} />
          </IconButton>
        </Tooltip>
      )}

      {/* ── Last sync status ───────────────────────────────────────────── */}
      {!isMobile && (
        <Tooltip
          title={
            runningScraper
              ? 'A scrape is running now'
              : failed > 0
                ? `${failed} dealership${failed === 1 ? '' : 's'} failed on the last run`
                : lastSync
                  ? `Last successful sync ${formatRelative(lastSync)}`
                  : 'No sync has run yet'
          }
        >
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              gap: 0.875,
              height: 36,
              px: 1.25,
              borderRadius: `${tk.radii.control}px`,
              border: `1px solid ${tk.surfaces.border}`,
              bgcolor: tk.surfaces.sunken,
              whiteSpace: 'nowrap',
              '@keyframes sync-dot': {
                '0%,100%': { opacity: 1, transform: 'scale(1)' },
                '50%': { opacity: 0.45, transform: 'scale(0.82)' },
              },
            }}
          >
            <Box
              sx={{
                width: 7,
                height: 7,
                borderRadius: '50%',
                bgcolor: syncTone,
                boxShadow: `0 0 0 3px ${alpha(syncTone, 0.16)}`,
                ...(runningScraper
                  ? {
                      animation: 'sync-dot 1.4s ease-in-out infinite',
                      '@media (prefers-reduced-motion: reduce)': { animation: 'none' },
                    }
                  : null),
              }}
            />
            <Typography variant="caption" sx={{ fontWeight: 560, color: 'text.secondary' }}>
              {runningScraper ? 'Syncing…' : lastSync ? formatRelative(lastSync) : 'Never synced'}
            </Typography>
          </Box>
        </Tooltip>
      )}

      <Tooltip title={mode === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}>
        <IconButton onClick={toggleTheme} aria-label="Toggle colour theme">
          {mode === 'dark' ? (
            <LightModeRoundedIcon sx={{ fontSize: 20 }} />
          ) : (
            <DarkModeRoundedIcon sx={{ fontSize: 20 }} />
          )}
        </IconButton>
      </Tooltip>

      <NotificationsMenu />

      <Button
        variant="contained"
        onClick={onRunScraper}
        disabled={runningScraper}
        startIcon={
          runningScraper ? <CircularProgress size={15} color="inherit" /> : <BoltRoundedIcon sx={{ fontSize: 18 }} />
        }
        sx={{ flexShrink: 0, px: { xs: 1.5, sm: 2 } }}
      >
        {isMobile ? 'Run' : runningScraper ? 'Starting…' : 'Run Scraper'}
      </Button>
    </Box>
  );
}
