import type { ReactNode } from 'react';
import SpaceDashboardRoundedIcon from '@mui/icons-material/SpaceDashboardRounded';
import StorefrontRoundedIcon from '@mui/icons-material/StorefrontRounded';
import DirectionsCarFilledRoundedIcon from '@mui/icons-material/DirectionsCarFilledRounded';
import BoltRoundedIcon from '@mui/icons-material/BoltRounded';
import SwapVertRoundedIcon from '@mui/icons-material/SwapVertRounded';
import InsightsRoundedIcon from '@mui/icons-material/InsightsRounded';
import FactCheckRoundedIcon from '@mui/icons-material/FactCheckRounded';
import SummarizeRoundedIcon from '@mui/icons-material/SummarizeRounded';
import TuneRoundedIcon from '@mui/icons-material/TuneRounded';

export interface NavItem {
  to: string;
  label: string;
  icon: ReactNode;
  /** Match the path exactly instead of by prefix. */
  end?: boolean;
  /** One-line explanation shown in the collapsed-sidebar tooltip and palette. */
  hint: string;
}

export interface NavGroup {
  /** Section heading; omit for the top group. */
  heading?: string;
  items: NavItem[];
}

export const NAV_GROUPS: NavGroup[] = [
  {
    items: [
      {
        to: '/',
        label: 'Dashboard',
        icon: <SpaceDashboardRoundedIcon />,
        end: true,
        hint: 'Platform health at a glance',
      },
    ],
  },
  {
    heading: 'Operations',
    items: [
      { to: '/dealers', label: 'Dealers', icon: <StorefrontRoundedIcon />, hint: 'Manage dealership sources' },
      { to: '/vehicles', label: 'Inventory', icon: <DirectionsCarFilledRoundedIcon />, hint: 'Every scraped vehicle' },
      { to: '/jobs', label: 'Scraping Jobs', icon: <BoltRoundedIcon />, hint: 'Monitor scrape runs' },
      { to: '/changes', label: 'Vehicle Changes', icon: <SwapVertRoundedIcon />, hint: 'Daily inventory movement' },
    ],
  },
  {
    heading: 'Insights',
    items: [
      {
        to: '/compatibility',
        label: 'Site Compatibility',
        icon: <FactCheckRoundedIcon />,
        hint: 'Which dealer sites we support, and which need custom work',
      },
      { to: '/analytics', label: 'Analytics', icon: <InsightsRoundedIcon />, hint: 'Trends and comparisons' },
      { to: '/reports', label: 'Reports', icon: <SummarizeRoundedIcon />, hint: 'Export platform data' },
    ],
  },
];

/** Pinned to the bottom of the sidebar, away from the primary destinations. */
export const NAV_FOOTER: NavItem[] = [
  { to: '/settings', label: 'Settings', icon: <TuneRoundedIcon />, hint: 'Appearance and preferences' },
];

export const ALL_NAV_ITEMS: NavItem[] = [...NAV_GROUPS.flatMap((g) => g.items), ...NAV_FOOTER];

/** Static path → label map for breadcrumb rendering. */
export const ROUTE_LABELS: Record<string, string> = {
  '': 'Dashboard',
  dealers: 'Dealers',
  vehicles: 'Inventory',
  jobs: 'Scraping Jobs',
  changes: 'Vehicle Changes',
  compatibility: 'Site Compatibility',
  analytics: 'Analytics',
  reports: 'Reports',
  settings: 'Settings',
  new: 'New',
  edit: 'Edit',
};

export function isNavActive(pathname: string, item: NavItem): boolean {
  if (item.end) return pathname === item.to;
  return pathname === item.to || pathname.startsWith(`${item.to}/`);
}
