import { useCallback, useEffect, useMemo, useState } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { Box, Drawer, Typography, useMediaQuery } from '@mui/material';
import { useTheme } from '@mui/material/styles';
import { Sidebar } from './Sidebar';
import { Topbar } from './Topbar';
import { CommandPalette } from './CommandPalette';
import { RunScraperContext } from './runScraperContext';
import { ConfirmDialog } from '../components/ui';
import { useDealerList } from '../hooks/useDealers';
import { useRunScraper } from '../hooks/useRunScraper';
import { useAppSettings } from '../theme/settings';
import { useTokens } from '../theme/useTokens';
import { layout } from '../theme/tokens';

export function AppLayout() {
  const theme = useTheme();
  const tk = useTokens();
  const location = useLocation();
  const { settings, setSettings } = useAppSettings();
  const isMobile = useMediaQuery(theme.breakpoints.down('md'));

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [runConfirmOpen, setRunConfirmOpen] = useState(false);

  const dealersQ = useDealerList();
  const runScraper = useRunScraper();

  const collapsed = !isMobile && settings.sidebarCollapsed;
  const sidebarWidth = collapsed ? layout.sidebarCollapsedWidth : layout.sidebarWidth;

  const activeDealers = (dealersQ.data ?? []).filter((d) => d.active);

  // ⌘K / Ctrl+K anywhere except while typing into a field.
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setPaletteOpen((open) => !open);
        return;
      }
      if (e.key === '/' && !paletteOpen) {
        const target = e.target as HTMLElement | null;
        const typing =
          target?.tagName === 'INPUT' || target?.tagName === 'TEXTAREA' || target?.isContentEditable;
        if (!typing) {
          e.preventDefault();
          setPaletteOpen(true);
        }
      }
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [paletteOpen]);

  const openRunConfirm = useCallback(() => setRunConfirmOpen(true), []);
  const runScraperCtx = useMemo(
    () => ({ open: openRunConfirm, isRunning: runScraper.isPending }),
    [openRunConfirm, runScraper.isPending],
  );

  return (
    <RunScraperContext.Provider value={runScraperCtx}>
    <Box sx={{ display: 'flex', minHeight: '100vh', bgcolor: 'background.default' }}>
      {/* Skip link — first tab stop for keyboard and screen-reader users. */}
      <Box
        component="a"
        href="#main-content"
        sx={{
          position: 'absolute',
          left: 8,
          top: -48,
          zIndex: 2000,
          px: 2,
          py: 1,
          borderRadius: `${tk.radii.control}px`,
          bgcolor: 'primary.main',
          color: 'primary.contrastText',
          fontSize: '0.875rem',
          fontWeight: 600,
          textDecoration: 'none',
          transition: `top ${tk.motion.base} ${tk.motion.ease}`,
          '&:focus': { top: 8 },
        }}
      >
        Skip to content
      </Box>

      {/* ── Sidebar ────────────────────────────────────────────────────── */}
      {!isMobile && (
        <Box
          sx={{
            width: sidebarWidth,
            flexShrink: 0,
            position: 'fixed',
            top: tk.layout.sidebarInset,
            left: tk.layout.sidebarInset,
            bottom: tk.layout.sidebarInset,
            zIndex: (t) => t.zIndex.appBar + 1,
            transition: `width ${tk.motion.slow} ${tk.motion.ease}`,
          }}
        >
          <Sidebar
            collapsed={collapsed}
            onToggleCollapsed={() => setSettings({ sidebarCollapsed: !settings.sidebarCollapsed })}
          />
        </Box>
      )}

      <Drawer
        open={isMobile && drawerOpen}
        onClose={() => setDrawerOpen(false)}
        ModalProps={{ keepMounted: true }}
        slotProps={{ paper: { sx: { width: layout.sidebarWidth, border: 0 } } }}
      >
        <Sidebar
          collapsed={false}
          showCollapseToggle={false}
          floating={false}
          onToggleCollapsed={() => undefined}
          onNavigate={() => setDrawerOpen(false)}
        />
      </Drawer>

      {/* ── Content column ─────────────────────────────────────────────── */}
      <Box
        sx={{
          flex: 1,
          minWidth: 0,
          ml: { xs: 0, md: `${sidebarWidth + tk.layout.sidebarInset * 2}px` },
          transition: `margin-left ${tk.motion.slow} ${tk.motion.ease}`,
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        <Topbar
          onOpenNav={() => setDrawerOpen(true)}
          onOpenSearch={() => setPaletteOpen(true)}
          onRunScraper={openRunConfirm}
          runningScraper={runScraper.isPending}
        />

        <Box
          component="main"
          id="main-content"
          tabIndex={-1}
          sx={{
            flex: 1,
            px: { xs: 2, sm: 3, lg: 4 },
            py: { xs: 3, lg: 4 },
            width: '100%',
            maxWidth: layout.contentMaxWidth,
            mx: 'auto',
            outline: 'none',
          }}
        >
          {/* Keyed on the route so each page cross-fades rather than snapping. */}
          <Box
            key={location.pathname}
            sx={{
              '@keyframes page-in': {
                from: { opacity: 0, transform: 'translate3d(0, 6px, 0)' },
                to: { opacity: 1, transform: 'none' },
              },
              animation: `page-in 280ms ${tk.motion.entrance} both`,
              '@media (prefers-reduced-motion: reduce)': { animation: 'none' },
            }}
          >
            <Outlet />
          </Box>
        </Box>
      </Box>

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} onRunScraper={openRunConfirm} />

      <ConfirmDialog
        open={runConfirmOpen}
        title="Run scraper on all active dealerships?"
        description={
          activeDealers.length === 0
            ? 'There are no active dealerships to sync. Activate at least one first.'
            : `This dispatches a scrape for ${activeDealers.length} active dealership${activeDealers.length === 1 ? '' : 's'}. Runs already in progress are left alone.`
        }
        confirmLabel={activeDealers.length === 0 ? 'Nothing to run' : 'Run scraper'}
        loading={runScraper.isPending}
        onClose={() => setRunConfirmOpen(false)}
        onConfirm={() => {
          if (activeDealers.length === 0) {
            setRunConfirmOpen(false);
            return;
          }
          runScraper.mutate(activeDealers, { onSettled: () => setRunConfirmOpen(false) });
        }}
      >
        {activeDealers.length > 0 && (
          <Typography variant="caption" color="text.secondary">
            Scrapes run in the background — you can keep working and watch progress on the Scraping Jobs page.
          </Typography>
        )}
      </ConfirmDialog>
    </Box>
    </RunScraperContext.Provider>
  );
}
