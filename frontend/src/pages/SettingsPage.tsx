import { useState, type ReactNode } from 'react';
import { useSnackbar } from 'notistack';
import {
  Box,
  Button,
  FormControlLabel,
  MenuItem,
  Select,
  Switch,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material';
import LightModeRoundedIcon from '@mui/icons-material/LightModeRounded';
import DarkModeRoundedIcon from '@mui/icons-material/DarkModeRounded';
import SettingsBrightnessRoundedIcon from '@mui/icons-material/SettingsBrightnessRounded';
import RestartAltRoundedIcon from '@mui/icons-material/RestartAltRounded';
import ContentCopyRoundedIcon from '@mui/icons-material/ContentCopyRounded';
import OpenInNewRoundedIcon from '@mui/icons-material/OpenInNew';
import { AppearIn, PageHeader, SectionCard, Surface } from '../components/ui';
import { useAppSettings, type Density, type ThemePreference } from '../theme/settings';
import { API_BASE_URL } from '../api/client';
import { useTokens } from '../theme/useTokens';

export function SettingsPage() {
  const tk = useTokens();
  const { settings, setSettings, resetSettings } = useAppSettings();
  const { enqueueSnackbar } = useSnackbar();
  const [profileDraft, setProfileDraft] = useState(settings.profileName);
  const [workspaceDraft, setWorkspaceDraft] = useState(settings.workspaceName);

  function copyEndpoint() {
    navigator.clipboard
      ?.writeText(API_BASE_URL)
      .then(() => enqueueSnackbar('API endpoint copied', { variant: 'success' }))
      .catch(() => enqueueSnackbar('Could not copy to clipboard', { variant: 'error' }));
  }

  return (
    <Box sx={{ maxWidth: tk.layout.formWidth }}>
      <PageHeader
        title="Settings"
        description="Appearance, defaults and workspace preferences. Everything here is stored on this device only."
      />

      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        <AppearIn>
          <SectionCard title="Appearance" description="How the interface looks on this device">
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3, pt: 0.5 }}>
              <SettingRow label="Theme" description="Match the system, or force light or dark">
                <ToggleButtonGroup
                  size="small"
                  exclusive
                  value={settings.theme}
                  onChange={(_, v: ThemePreference | null) => v && setSettings({ theme: v })}
                >
                  <ToggleButton value="light" sx={{ gap: 0.75, px: 1.5 }}>
                    <LightModeRoundedIcon sx={{ fontSize: 16 }} /> Light
                  </ToggleButton>
                  <ToggleButton value="dark" sx={{ gap: 0.75, px: 1.5 }}>
                    <DarkModeRoundedIcon sx={{ fontSize: 16 }} /> Dark
                  </ToggleButton>
                  <ToggleButton value="system" sx={{ gap: 0.75, px: 1.5 }}>
                    <SettingsBrightnessRoundedIcon sx={{ fontSize: 16 }} /> System
                  </ToggleButton>
                </ToggleButtonGroup>
              </SettingRow>

              <SettingRow label="Density" description="Row height and padding across tables and cards">
                <ToggleButtonGroup
                  size="small"
                  exclusive
                  value={settings.density}
                  onChange={(_, v: Density | null) => v && setSettings({ density: v })}
                >
                  <ToggleButton value="comfortable" sx={{ px: 2 }}>
                    Comfortable
                  </ToggleButton>
                  <ToggleButton value="compact" sx={{ px: 2 }}>
                    Compact
                  </ToggleButton>
                </ToggleButtonGroup>
              </SettingRow>

              <SettingRow label="Reduce motion" description="Turn off count-up, entrance and hover animations">
                <Switch
                  checked={settings.reduceMotion}
                  onChange={(e) => setSettings({ reduceMotion: e.target.checked })}
                />
              </SettingRow>
            </Box>
          </SectionCard>
        </AppearIn>

        <AppearIn index={1}>
          <SectionCard title="Data & performance" description="Defaults for tables and background updates">
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3, pt: 0.5 }}>
              <SettingRow label="Default page size" description="Rows per page for new tables">
                <Select
                  size="small"
                  value={settings.pageSize}
                  onChange={(e) => setSettings({ pageSize: Number(e.target.value) })}
                  sx={{ minWidth: 100 }}
                >
                  {[10, 25, 50, 100].map((n) => (
                    <MenuItem key={n} value={n}>
                      {n} rows
                    </MenuItem>
                  ))}
                </Select>
              </SettingRow>

              <SettingRow label="Background refresh" description="Automatically re-fetch dashboard and job data">
                <Select
                  size="small"
                  value={settings.autoRefreshMs}
                  onChange={(e) => setSettings({ autoRefreshMs: Number(e.target.value) })}
                  sx={{ minWidth: 140 }}
                >
                  <MenuItem value={0}>Off</MenuItem>
                  <MenuItem value={15_000}>Every 15s</MenuItem>
                  <MenuItem value={30_000}>Every 30s</MenuItem>
                  <MenuItem value={60_000}>Every minute</MenuItem>
                  <MenuItem value={300_000}>Every 5 minutes</MenuItem>
                </Select>
              </SettingRow>
            </Box>
          </SectionCard>
        </AppearIn>

        <AppearIn index={2}>
          <SectionCard title="Profile & workspace" description="Shown in the sidebar and workspace switcher">
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2.5, pt: 0.5 }}>
              <Box sx={{ display: 'flex', gap: 1.5, alignItems: 'flex-start', flexWrap: 'wrap' }}>
                <TextField
                  label="Display name"
                  size="small"
                  value={profileDraft}
                  onChange={(e) => setProfileDraft(e.target.value)}
                  sx={{ flex: 1, minWidth: 200 }}
                />
                <TextField
                  label="Workspace name"
                  size="small"
                  value={workspaceDraft}
                  onChange={(e) => setWorkspaceDraft(e.target.value)}
                  sx={{ flex: 1, minWidth: 200 }}
                />
                <Button
                  variant="contained"
                  sx={{ height: 40, mt: 0 }}
                  disabled={!profileDraft.trim() || !workspaceDraft.trim()}
                  onClick={() => {
                    setSettings({ profileName: profileDraft.trim(), workspaceName: workspaceDraft.trim() });
                    enqueueSnackbar('Preferences saved', { variant: 'success' });
                  }}
                >
                  Save
                </Button>
              </Box>
              <FormControlLabel
                control={
                  <Switch
                    checked={settings.sidebarCollapsed}
                    onChange={(e) => setSettings({ sidebarCollapsed: e.target.checked })}
                  />
                }
                label={
                  <Box>
                    <Typography variant="body2" sx={{ fontWeight: 560 }}>
                      Collapse sidebar by default
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      Icons only, expandable with the sidebar toggle
                    </Typography>
                  </Box>
                }
                sx={{ ml: 0, gap: 1 }}
              />
            </Box>
          </SectionCard>
        </AppearIn>

        <AppearIn index={3}>
          <SectionCard title="Connection" description="The API this workspace talks to">
            <Surface tone="sunken" sx={{ p: 2, display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap' }}>
              <Typography
                variant="body2"
                sx={{ fontFamily: 'ui-monospace, monospace', flex: 1, minWidth: 200, wordBreak: 'break-all' }}
              >
                {API_BASE_URL}
              </Typography>
              <Button size="small" startIcon={<ContentCopyRoundedIcon sx={{ fontSize: 15 }} />} onClick={copyEndpoint}>
                Copy
              </Button>
              <Button
                size="small"
                startIcon={<OpenInNewRoundedIcon sx={{ fontSize: 15 }} />}
                href={`${API_BASE_URL}/docs`}
                target="_blank"
                rel="noopener noreferrer"
              >
                API docs
              </Button>
            </Surface>
          </SectionCard>
        </AppearIn>

        <AppearIn index={4}>
          <Box sx={{ display: 'flex', justifyContent: 'flex-end' }}>
            <Button
              color="inherit"
              startIcon={<RestartAltRoundedIcon />}
              onClick={() => {
                resetSettings();
                setProfileDraft('Operator');
                setWorkspaceDraft('Inventory Hub');
                enqueueSnackbar('Settings reset to defaults', { variant: 'info' });
              }}
            >
              Reset all settings
            </Button>
          </Box>
        </AppearIn>
      </Box>
    </Box>
  );
}

function SettingRow({
  label,
  description,
  children,
}: {
  label: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 3, flexWrap: 'wrap' }}>
      <Box sx={{ minWidth: 200 }}>
        <Typography variant="body2" sx={{ fontWeight: 560 }}>
          {label}
        </Typography>
        <Typography variant="caption" color="text.secondary">
          {description}
        </Typography>
      </Box>
      {children}
    </Box>
  );
}
