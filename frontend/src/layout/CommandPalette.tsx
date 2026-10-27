import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Box, CircularProgress, Dialog, InputBase, Typography } from '@mui/material';
import { alpha, useTheme } from '@mui/material/styles';
import SearchRoundedIcon from '@mui/icons-material/SearchRounded';
import StorefrontRoundedIcon from '@mui/icons-material/StorefrontRounded';
import DirectionsCarFilledRoundedIcon from '@mui/icons-material/DirectionsCarFilledRounded';
import BoltRoundedIcon from '@mui/icons-material/BoltRounded';
import AddRoundedIcon from '@mui/icons-material/AddRounded';
import DarkModeRoundedIcon from '@mui/icons-material/DarkModeRounded';
import LightModeRoundedIcon from '@mui/icons-material/LightModeRounded';
import { ALL_NAV_ITEMS } from './navConfig';
import { listVehicles } from '../api/vehicles';
import { useDealerList } from '../hooks/useDealers';
import { useDebouncedValue } from '../utils/useDebouncedValue';
import { useTokens } from '../theme/useTokens';
import { useAppSettings } from '../theme/settings';
import { formatMoney, vehicleTitle } from '../utils/format';
import { Kbd } from './Kbd';

interface Command {
  id: string;
  group: string;
  label: string;
  hint?: string;
  icon: ReactNode;
  run: () => void;
}

interface Props {
  open: boolean;
  onClose: () => void;
  /** Fired when the palette's "Run scraper" command is chosen. */
  onRunScraper: () => void;
}

/**
 * ⌘K / Ctrl+K global search.
 *
 * Navigation and dealers are matched locally against already-cached data;
 * vehicles hit the server's own search so VINs and trims resolve too.
 */
export function CommandPalette({ open, onClose, onRunScraper }: Props) {
  const tk = useTokens();
  const theme = useTheme();
  const navigate = useNavigate();
  const { mode, toggleTheme } = useAppSettings();

  const [query, setQuery] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);

  const debounced = useDebouncedValue(query, 220);
  const dealersQ = useDealerList();

  const vehiclesQ = useQuery({
    queryKey: ['vehicles', 'palette', debounced],
    queryFn: () => listVehicles({ q: debounced, page_size: 6, sort: 'last_seen_at', order: 'desc' }),
    enabled: open && debounced.trim().length >= 2,
    staleTime: 30_000,
  });

  useEffect(() => {
    if (!open) {
      setQuery('');
      setActiveIndex(0);
    }
  }, [open]);

  const go = useMemo(
    () => (to: string) => {
      navigate(to);
      onClose();
    },
    [navigate, onClose],
  );

  const commands = useMemo<Command[]>(() => {
    const q = query.trim().toLowerCase();
    const matches = (text: string) => !q || text.toLowerCase().includes(q);

    const actions: Command[] = [
      {
        id: 'action:add-dealer',
        group: 'Actions',
        label: 'Add dealership',
        hint: 'Create a new scrape source',
        icon: <AddRoundedIcon />,
        run: () => go('/dealers/new'),
      },
      {
        id: 'action:run-scraper',
        group: 'Actions',
        label: 'Run scraper on all active dealers',
        hint: 'Dispatch a sync everywhere',
        icon: <BoltRoundedIcon />,
        run: () => {
          onClose();
          onRunScraper();
        },
      },
      {
        id: 'action:theme',
        group: 'Actions',
        label: mode === 'dark' ? 'Switch to light theme' : 'Switch to dark theme',
        icon: mode === 'dark' ? <LightModeRoundedIcon /> : <DarkModeRoundedIcon />,
        run: () => {
          toggleTheme();
          onClose();
        },
      },
    ].filter((c) => matches(c.label));

    const nav: Command[] = ALL_NAV_ITEMS.filter((i) => matches(i.label) || matches(i.hint)).map((item) => ({
      id: `nav:${item.to}`,
      group: 'Go to',
      label: item.label,
      hint: item.hint,
      icon: item.icon,
      run: () => go(item.to),
    }));

    const dealers: Command[] = (dealersQ.data ?? [])
      .filter((d) => matches(d.name) || matches(d.base_url) || matches(d.city ?? ''))
      .slice(0, 6)
      .map((d) => ({
        id: `dealer:${d.id}`,
        group: 'Dealerships',
        label: d.name,
        hint: [d.city, d.website_provider, `${d.inventory_count.toLocaleString()} vehicles`]
          .filter(Boolean)
          .join(' · '),
        icon: <StorefrontRoundedIcon />,
        run: () => go(`/dealers/${d.id}`),
      }));

    const vehicles: Command[] = (vehiclesQ.data?.items ?? []).map((v) => ({
      id: `vehicle:${v.id}`,
      group: 'Vehicles',
      label: vehicleTitle(v),
      hint: [v.vin, formatMoney(v.price, v.currency)].filter(Boolean).join(' · '),
      icon: <DirectionsCarFilledRoundedIcon />,
      run: () => go(`/vehicles/${v.id}`),
    }));

    return [...nav, ...dealers, ...vehicles, ...actions];
  }, [query, dealersQ.data, vehiclesQ.data, go, mode, toggleTheme, onClose, onRunScraper]);

  useEffect(() => setActiveIndex(0), [commands.length]);

  // Keep the highlighted row inside the scroll viewport during keyboard nav.
  useEffect(() => {
    const node = listRef.current?.querySelector<HTMLElement>(`[data-index="${activeIndex}"]`);
    node?.scrollIntoView({ block: 'nearest' });
  }, [activeIndex]);

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIndex((i) => (commands.length ? (i + 1) % commands.length : 0));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIndex((i) => (commands.length ? (i - 1 + commands.length) % commands.length : 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      commands[activeIndex]?.run();
    }
  }

  let renderedGroup = '';

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="sm"
      fullWidth
      slotProps={{
        paper: {
          sx: {
            position: 'fixed',
            top: { xs: 24, sm: 88 },
            m: 0,
            width: 'min(640px, calc(100vw - 32px))',
            alignSelf: 'flex-start',
            borderRadius: `${tk.radii.card + 2}px`,
            overflow: 'hidden',
          },
        },
        transition: { timeout: { enter: 180, exit: 120 } },
      }}
    >
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, px: 2, py: 1.75, borderBottom: `1px solid ${tk.surfaces.border}` }}>
        <SearchRoundedIcon sx={{ color: 'text.disabled', fontSize: 20 }} />
        <InputBase
          autoFocus
          fullWidth
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Search dealers, vehicles, VINs, pages…"
          inputProps={{ 'aria-label': 'Global search' }}
          sx={{ fontSize: '0.9375rem', '& input::placeholder': { color: 'text.disabled', opacity: 1 } }}
        />
        {vehiclesQ.isFetching && <CircularProgress size={15} />}
        <Kbd>Esc</Kbd>
      </Box>

      <Box ref={listRef} sx={{ maxHeight: 420, overflowY: 'auto', p: 1 }}>
        {commands.length === 0 && (
          <Box sx={{ px: 2, py: 5, textAlign: 'center' }}>
            <Typography variant="body2" color="text.secondary">
              No matches for “{query}”
            </Typography>
            <Typography variant="caption" color="text.disabled">
              Try a dealership name, VIN, make or model.
            </Typography>
          </Box>
        )}

        {commands.map((cmd, i) => {
          const showHeading = cmd.group !== renderedGroup;
          renderedGroup = cmd.group;
          const active = i === activeIndex;
          return (
            <Box key={cmd.id}>
              {showHeading && (
                <Typography
                  variant="overline"
                  sx={{ display: 'block', px: 1.5, pt: i === 0 ? 0.5 : 1.5, pb: 0.5, color: 'text.disabled' }}
                >
                  {cmd.group}
                </Typography>
              )}
              <Box
                data-index={i}
                role="button"
                tabIndex={-1}
                onMouseMove={() => setActiveIndex(i)}
                onClick={cmd.run}
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 1.5,
                  px: 1.5,
                  py: 1,
                  borderRadius: `${tk.radii.sm}px`,
                  cursor: 'pointer',
                  bgcolor: active ? alpha(theme.palette.primary.main, theme.palette.mode === 'light' ? 0.08 : 0.16) : 'transparent',
                  color: active ? 'text.primary' : 'text.secondary',
                  '& > svg': { fontSize: 18, color: active ? theme.palette.primary.main : theme.palette.text.disabled },
                }}
              >
                {cmd.icon}
                <Box sx={{ minWidth: 0, flex: 1 }}>
                  <Typography variant="body2" noWrap sx={{ fontWeight: 540, color: 'text.primary' }}>
                    {cmd.label}
                  </Typography>
                  {cmd.hint && (
                    <Typography variant="caption" color="text.secondary" noWrap sx={{ display: 'block' }}>
                      {cmd.hint}
                    </Typography>
                  )}
                </Box>
                {active && <Kbd>↵</Kbd>}
              </Box>
            </Box>
          );
        })}
      </Box>

      <Box
        sx={{
          px: 2,
          py: 1.25,
          borderTop: `1px solid ${tk.surfaces.border}`,
          bgcolor: tk.surfaces.sunken,
          display: 'flex',
          gap: 2,
          alignItems: 'center',
        }}
      >
        <Typography variant="caption" color="text.disabled" sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
          <Kbd>↑</Kbd>
          <Kbd>↓</Kbd> navigate
        </Typography>
        <Typography variant="caption" color="text.disabled" sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
          <Kbd>↵</Kbd> open
        </Typography>
      </Box>
    </Dialog>
  );
}
