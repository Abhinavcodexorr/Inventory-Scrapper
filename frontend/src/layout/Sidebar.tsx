import { useState } from 'react';
import { Link as RouterLink, useLocation, useNavigate } from 'react-router-dom';
import {
  Avatar,
  Box,
  Divider,
  ListItemIcon,
  Menu,
  MenuItem,
  Tooltip,
  Typography,
} from '@mui/material';
import { alpha, useTheme } from '@mui/material/styles';
import UnfoldMoreRoundedIcon from '@mui/icons-material/UnfoldMoreRounded';
import KeyboardDoubleArrowLeftRoundedIcon from '@mui/icons-material/KeyboardDoubleArrowLeftRounded';
import KeyboardDoubleArrowRightRoundedIcon from '@mui/icons-material/KeyboardDoubleArrowRightRounded';
import ContentCopyRoundedIcon from '@mui/icons-material/ContentCopyRounded';
import OpenInNewRoundedIcon from '@mui/icons-material/OpenInNew';
import TuneRoundedIcon from '@mui/icons-material/TuneRounded';
import LogoutRoundedIcon from '@mui/icons-material/LogoutRounded';
import CheckRoundedIcon from '@mui/icons-material/CheckRounded';
import { useSnackbar } from 'notistack';
import { Logo } from './Logo';
import { NAV_FOOTER, NAV_GROUPS, isNavActive, type NavItem } from './navConfig';
import { useTokens } from '../theme/useTokens';
import { useAppSettings } from '../theme/settings';
import { useAuth } from '../auth/AuthContext';
import { API_BASE_URL } from '../api/client';
import { initials } from '../utils/format';

interface Props {
  collapsed: boolean;
  onToggleCollapsed: () => void;
  /** Called after navigation so the mobile drawer can close itself. */
  onNavigate?: () => void;
  /** Mobile drawer never collapses — hide the collapse control there. */
  showCollapseToggle?: boolean;
  /** Desktop rail floats with rounded corners and a glass background. The
   *  mobile drawer variant stays flush inside its own drawer paper. */
  floating?: boolean;
}

export function Sidebar({
  collapsed,
  onToggleCollapsed,
  onNavigate,
  showCollapseToggle = true,
  floating = true,
}: Props) {
  const tk = useTokens();
  const theme = useTheme();
  const location = useLocation();
  const navigate = useNavigate();
  const { settings, setSettings } = useAppSettings();
  const { user, signOut } = useAuth();
  const { enqueueSnackbar } = useSnackbar();

  const [workspaceAnchor, setWorkspaceAnchor] = useState<HTMLElement | null>(null);
  const [profileAnchor, setProfileAnchor] = useState<HTMLElement | null>(null);

  const apiHost = (() => {
    try {
      return new URL(API_BASE_URL).host;
    } catch {
      return API_BASE_URL;
    }
  })();

  function copyEndpoint() {
    navigator.clipboard
      ?.writeText(API_BASE_URL)
      .then(() => enqueueSnackbar('API endpoint copied', { variant: 'success' }))
      .catch(() => enqueueSnackbar('Could not copy to clipboard', { variant: 'error' }));
    setWorkspaceAnchor(null);
  }

  return (
    <Box
      component="nav"
      aria-label="Primary"
      sx={{
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        bgcolor: floating ? alpha(tk.surfaces.nav, 0.9) : tk.surfaces.nav,
        overflow: 'hidden',
        ...(floating
          ? {
              borderRadius: `${tk.radii.lg}px`,
              border: `1px solid ${tk.surfaces.cardBorder}`,
              boxShadow: tk.elevation.md,
              backdropFilter: 'blur(20px) saturate(160%)',
            }
          : { borderRight: `1px solid ${tk.surfaces.border}` }),
      }}
    >
      {/* ── Workspace switcher ─────────────────────────────────────────── */}
      <Box sx={{ p: 1.5, pb: 1 }}>
        <Box
          component="button"
          type="button"
          onClick={(e) => setWorkspaceAnchor(e.currentTarget)}
          aria-label="Workspace menu"
          sx={{
            width: '100%',
            display: 'flex',
            alignItems: 'center',
            gap: 1.25,
            p: 1,
            border: 0,
            borderRadius: `${tk.radii.control}px`,
            bgcolor: 'transparent',
            cursor: 'pointer',
            textAlign: 'left',
            color: 'inherit',
            font: 'inherit',
            transition: `background-color ${tk.motion.fast} ${tk.motion.ease}`,
            '&:hover': { bgcolor: tk.surfaces.hover },
            '&:focus-visible': { outline: 'none', boxShadow: tk.focusRing },
          }}
        >
          <Logo size={30} />
          {!collapsed && (
            <>
              <Box sx={{ minWidth: 0, flex: 1 }}>
                <Typography variant="subtitle2" noWrap sx={{ fontWeight: 660, letterSpacing: '-0.015em' }}>
                  {settings.workspaceName}
                </Typography>
                <Typography variant="caption" color="text.secondary" noWrap sx={{ display: 'block', lineHeight: 1.3 }}>
                  {apiHost}
                </Typography>
              </Box>
              <UnfoldMoreRoundedIcon sx={{ fontSize: 16, color: 'text.disabled' }} />
            </>
          )}
        </Box>
      </Box>

      <Menu
        anchorEl={workspaceAnchor}
        open={Boolean(workspaceAnchor)}
        onClose={() => setWorkspaceAnchor(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
        transformOrigin={{ vertical: 'top', horizontal: 'left' }}
        slotProps={{ paper: { sx: { minWidth: 248 } } }}
      >
        <Box sx={{ px: 1.25, py: 1 }}>
          <Typography variant="overline" color="text.secondary">
            Connected API
          </Typography>
          <Typography variant="body2" sx={{ fontWeight: 560, wordBreak: 'break-all', mt: 0.25 }}>
            {API_BASE_URL}
          </Typography>
        </Box>
        <Divider sx={{ my: 0.5 }} />
        <MenuItem onClick={copyEndpoint}>
          <ListItemIcon>
            <ContentCopyRoundedIcon fontSize="small" />
          </ListItemIcon>
          Copy endpoint
        </MenuItem>
        <MenuItem
          component="a"
          href={`${API_BASE_URL}/docs`}
          target="_blank"
          rel="noopener noreferrer"
          onClick={() => setWorkspaceAnchor(null)}
        >
          <ListItemIcon>
            <OpenInNewRoundedIcon fontSize="small" />
          </ListItemIcon>
          Open API docs
        </MenuItem>
        <MenuItem
          onClick={() => {
            setWorkspaceAnchor(null);
            navigate('/settings');
          }}
        >
          <ListItemIcon>
            <TuneRoundedIcon fontSize="small" />
          </ListItemIcon>
          Workspace settings
        </MenuItem>
      </Menu>

      {/* ── Destinations ───────────────────────────────────────────────── */}
      <Box sx={{ flex: 1, overflowY: 'auto', overflowX: 'hidden', px: 1.5, py: 0.5 }}>
        {NAV_GROUPS.map((group, gi) => (
          <Box key={group.heading ?? `group-${gi}`} sx={{ mb: 2 }}>
            {group.heading && !collapsed && (
              <Typography
                variant="overline"
                sx={{ display: 'block', px: 1.25, pb: 0.75, pt: 0.5, color: 'text.disabled', fontSize: '0.6875rem' }}
              >
                {group.heading}
              </Typography>
            )}
            {group.heading && collapsed && <Divider sx={{ mx: 1, my: 1.25 }} />}
            <Box component="ul" sx={{ listStyle: 'none', m: 0, p: 0, display: 'grid', gap: 0.25 }}>
              {group.items.map((item) => (
                <NavRow
                  key={item.to}
                  item={item}
                  active={isNavActive(location.pathname, item)}
                  collapsed={collapsed}
                  onNavigate={onNavigate}
                />
              ))}
            </Box>
          </Box>
        ))}
      </Box>

      {/* ── Footer: settings, collapse, profile ────────────────────────── */}
      <Box sx={{ px: 1.5, pb: 1.5, pt: 1, borderTop: `1px solid ${tk.surfaces.border}` }}>
        <Box component="ul" sx={{ listStyle: 'none', m: 0, p: 0, mb: 1, display: 'grid', gap: 0.25 }}>
          {NAV_FOOTER.map((item) => (
            <NavRow
              key={item.to}
              item={item}
              active={isNavActive(location.pathname, item)}
              collapsed={collapsed}
              onNavigate={onNavigate}
            />
          ))}
          {showCollapseToggle && (
            <Box component="li">
              <Tooltip title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'} placement="right">
                <Box
                  component="button"
                  type="button"
                  onClick={onToggleCollapsed}
                  aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
                  sx={{
                    width: '100%',
                    display: 'flex',
                    alignItems: 'center',
                    gap: 1.5,
                    px: 1.25,
                    py: 1,
                    border: 0,
                    borderRadius: `${tk.radii.control}px`,
                    bgcolor: 'transparent',
                    color: theme.palette.text.secondary,
                    cursor: 'pointer',
                    font: 'inherit',
                    fontSize: '0.875rem',
                    justifyContent: collapsed ? 'center' : 'flex-start',
                    transition: `background-color ${tk.motion.fast} ${tk.motion.ease}, color ${tk.motion.fast} ${tk.motion.ease}`,
                    '&:hover': { bgcolor: tk.surfaces.hover, color: theme.palette.text.primary },
                    '&:focus-visible': { outline: 'none', boxShadow: tk.focusRing },
                  }}
                >
                  {collapsed ? (
                    <KeyboardDoubleArrowRightRoundedIcon sx={{ fontSize: 20 }} />
                  ) : (
                    <KeyboardDoubleArrowLeftRoundedIcon sx={{ fontSize: 20 }} />
                  )}
                  {!collapsed && <span>Collapse</span>}
                </Box>
              </Tooltip>
            </Box>
          )}
        </Box>

        <Divider sx={{ mb: 1 }} />

        <Tooltip title={collapsed ? settings.profileName : ''} placement="right">
          <Box
            component="button"
            type="button"
            onClick={(e) => setProfileAnchor(e.currentTarget)}
            aria-label="Account menu"
            sx={{
              width: '100%',
              display: 'flex',
              alignItems: 'center',
              gap: 1.25,
              p: 1,
              border: 0,
              borderRadius: `${tk.radii.control}px`,
              bgcolor: 'transparent',
              cursor: 'pointer',
              textAlign: 'left',
              color: 'inherit',
              font: 'inherit',
              justifyContent: collapsed ? 'center' : 'flex-start',
              transition: `background-color ${tk.motion.fast} ${tk.motion.ease}`,
              '&:hover': { bgcolor: tk.surfaces.hover },
              '&:focus-visible': { outline: 'none', boxShadow: tk.focusRing },
            }}
          >
            <Avatar
              sx={{
                width: 30,
                height: 30,
                fontSize: '0.75rem',
                bgcolor: alpha(theme.palette.primary.main, 0.14),
                color: theme.palette.primary.main,
                fontWeight: 700,
              }}
            >
              {initials(settings.profileName)}
            </Avatar>
            {!collapsed && (
              <>
                <Box sx={{ minWidth: 0, flex: 1 }}>
                  <Typography variant="body2" noWrap sx={{ fontWeight: 560 }}>
                    {settings.profileName}
                  </Typography>
                  <Typography variant="caption" color="text.secondary" noWrap sx={{ display: 'block', lineHeight: 1.3 }}>
                    Operations
                  </Typography>
                </Box>
                <UnfoldMoreRoundedIcon sx={{ fontSize: 16, color: 'text.disabled' }} />
              </>
            )}
          </Box>
        </Tooltip>
      </Box>

      <Menu
        anchorEl={profileAnchor}
        open={Boolean(profileAnchor)}
        onClose={() => setProfileAnchor(null)}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        transformOrigin={{ vertical: 'bottom', horizontal: 'left' }}
        slotProps={{ paper: { sx: { minWidth: 220 } } }}
      >
        <Box sx={{ px: 1.25, py: 1 }}>
          <Typography variant="body2" sx={{ fontWeight: 600 }}>
            {settings.profileName}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {user?.email ?? 'Not signed in'}
          </Typography>
        </Box>
        <Divider sx={{ my: 0.5 }} />
        <MenuItem
          onClick={() => {
            setProfileAnchor(null);
            navigate('/settings');
          }}
        >
          <ListItemIcon>
            <TuneRoundedIcon fontSize="small" />
          </ListItemIcon>
          Preferences
        </MenuItem>
        <MenuItem
          onClick={() => {
            setSettings({ density: settings.density === 'compact' ? 'comfortable' : 'compact' });
            setProfileAnchor(null);
          }}
        >
          <ListItemIcon>
            {settings.density === 'compact' ? <CheckRoundedIcon fontSize="small" /> : <span style={{ width: 20 }} />}
          </ListItemIcon>
          Compact density
        </MenuItem>
        <Divider sx={{ my: 0.5 }} />
        <MenuItem
          onClick={async () => {
            setProfileAnchor(null);
            try {
              await signOut();
            } finally {
              // Navigate regardless: signOut already clears the local session,
              // so leaving the operator on a dead page after a network blip
              // would be worse than the redirect.
              navigate('/login', { replace: true });
            }
          }}
        >
          <ListItemIcon>
            <LogoutRoundedIcon fontSize="small" />
          </ListItemIcon>
          Sign out
        </MenuItem>
      </Menu>
    </Box>
  );
}

/** A single navigation destination with its active rail indicator. */
function NavRow({
  item,
  active,
  collapsed,
  onNavigate,
}: {
  item: NavItem;
  active: boolean;
  collapsed: boolean;
  onNavigate?: () => void;
}) {
  const tk = useTokens();
  const theme = useTheme();

  const row = (
    <Box
      component={RouterLink}
      to={item.to}
      onClick={onNavigate}
      aria-current={active ? 'page' : undefined}
      sx={{
        position: 'relative',
        display: 'flex',
        alignItems: 'center',
        gap: 1.5,
        px: 1.25,
        py: 1,
        borderRadius: `${tk.radii.control}px`,
        textDecoration: 'none',
        fontSize: '0.875rem',
        fontWeight: active ? 600 : 500,
        color: active ? theme.palette.primary.main : theme.palette.text.secondary,
        bgcolor: active ? tk.surfaces.selected : 'transparent',
        justifyContent: collapsed ? 'center' : 'flex-start',
        transition: `background-color ${tk.motion.fast} ${tk.motion.ease}, color ${tk.motion.fast} ${tk.motion.ease}`,
        '& svg': { fontSize: 20, transition: `transform ${tk.motion.base} ${tk.motion.ease}` },
        '&:hover': {
          bgcolor: active ? tk.surfaces.selected : tk.surfaces.hover,
          color: active ? theme.palette.primary.main : theme.palette.text.primary,
        },
        '&:hover svg': { transform: 'scale(1.08)' },
        '&:focus-visible': { outline: 'none', boxShadow: tk.focusRing },
        // Active rail: a short accent bar bleeding off the left edge.
        '&::before': active
          ? {
              content: '""',
              position: 'absolute',
              left: -6,
              top: '50%',
              transform: 'translateY(-50%)',
              width: 3,
              height: 18,
              borderRadius: 999,
              bgcolor: theme.palette.primary.main,
            }
          : undefined,
      }}
    >
      {item.icon}
      {!collapsed && <span>{item.label}</span>}
    </Box>
  );

  return (
    <Box component="li">
      {collapsed ? (
        <Tooltip title={item.label} placement="right">
          {row}
        </Tooltip>
      ) : (
        row
      )}
    </Box>
  );
}
