import { createContext, useContext } from 'react';

export type ThemePreference = 'light' | 'dark' | 'system';
export type Density = 'comfortable' | 'compact';

/** User-controlled presentation preferences. Persisted to localStorage. */
export interface AppSettings {
  /** Requested theme; `system` follows the OS. */
  theme: ThemePreference;
  /** Row height / padding across every table and card. */
  density: Density;
  /** Default rows per page for paginated tables. */
  pageSize: number;
  /** Background refetch cadence in ms. 0 disables polling. */
  autoRefreshMs: number;
  /** Suppress entrance/count-up animation regardless of OS setting. */
  reduceMotion: boolean;
  /** Display name shown in the sidebar profile and user menu. */
  profileName: string;
  /** Label for this deployment, shown in the workspace switcher. */
  workspaceName: string;
  /** Collapse the sidebar to icons only. */
  sidebarCollapsed: boolean;
}

export const DEFAULT_SETTINGS: AppSettings = {
  theme: 'system',
  density: 'comfortable',
  pageSize: 25,
  autoRefreshMs: 0,
  reduceMotion: false,
  profileName: 'Operator',
  workspaceName: 'Inventory Hub',
  sidebarCollapsed: false,
};

export const SETTINGS_STORAGE_KEY = 'inventory-hub.settings.v1';

export interface AppSettingsContextValue {
  settings: AppSettings;
  /** Resolved mode after applying the `system` preference. */
  mode: 'light' | 'dark';
  setSettings: (patch: Partial<AppSettings>) => void;
  resetSettings: () => void;
  toggleTheme: () => void;
}

export const AppSettingsContext = createContext<AppSettingsContextValue | null>(null);

export function useAppSettings(): AppSettingsContextValue {
  const ctx = useContext(AppSettingsContext);
  if (!ctx) throw new Error('useAppSettings must be used inside <AppThemeProvider>');
  return ctx;
}

export function readStoredSettings(): AppSettings {
  if (typeof window === 'undefined') return DEFAULT_SETTINGS;
  try {
    const raw = window.localStorage.getItem(SETTINGS_STORAGE_KEY);
    if (!raw) return DEFAULT_SETTINGS;
    const parsed = JSON.parse(raw) as Partial<AppSettings>;
    return { ...DEFAULT_SETTINGS, ...parsed };
  } catch {
    return DEFAULT_SETTINGS;
  }
}
