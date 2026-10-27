import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { CssBaseline, ThemeProvider } from '@mui/material';
import { createAppTheme } from './createAppTheme';
import {
  AppSettingsContext,
  DEFAULT_SETTINGS,
  SETTINGS_STORAGE_KEY,
  readStoredSettings,
  type AppSettings,
} from './settings';

/** Live subscription to the OS colour-scheme so `theme: 'system'` stays honest. */
function usePrefersDark(): boolean {
  const [prefersDark, setPrefersDark] = useState(
    () => typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches,
  );

  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const onChange = (e: MediaQueryListEvent) => setPrefersDark(e.matches);
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);

  return prefersDark;
}

export function AppThemeProvider({ children }: { children: ReactNode }) {
  const [settings, setSettingsState] = useState<AppSettings>(readStoredSettings);
  const prefersDark = usePrefersDark();

  const mode = settings.theme === 'system' ? (prefersDark ? 'dark' : 'light') : settings.theme;

  useEffect(() => {
    try {
      window.localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(settings));
    } catch {
      /* storage disabled — preferences simply don't persist */
    }
  }, [settings]);

  // Keep the document in sync so the browser paints native UI (scrollbars, form
  // controls, address bar on mobile) in the matching scheme.
  useEffect(() => {
    document.documentElement.dataset.theme = mode;
    document.documentElement.style.colorScheme = mode;
  }, [mode]);

  const setSettings = useCallback((patch: Partial<AppSettings>) => {
    setSettingsState((prev) => ({ ...prev, ...patch }));
  }, []);

  const resetSettings = useCallback(() => setSettingsState(DEFAULT_SETTINGS), []);

  const toggleTheme = useCallback(() => {
    setSettingsState((prev) => ({
      ...prev,
      theme: (prev.theme === 'system' ? (prefersDark ? 'light' : 'dark') : prev.theme === 'light' ? 'dark' : 'light'),
    }));
  }, [prefersDark]);

  const theme = useMemo(() => createAppTheme(mode, settings.density), [mode, settings.density]);

  const ctx = useMemo(
    () => ({ settings, mode, setSettings, resetSettings, toggleTheme }),
    [settings, mode, setSettings, resetSettings, toggleTheme],
  );

  return (
    <AppSettingsContext.Provider value={ctx}>
      <ThemeProvider theme={theme}>
        <CssBaseline />
        {children}
      </ThemeProvider>
    </AppSettingsContext.Provider>
  );
}
