import { useCallback, useMemo } from 'react';
import { useQueries, useQuery } from '@tanstack/react-query';
import { listDealerRuns, listDealers } from '../api/dealers';
import { getDealerStats, getDealerTrend, getSummary } from '../api/dashboard';
import { useAppSettings } from '../theme/settings';
import type { Dealer, DealerStats, Snapshot, SyncRun } from '../types';

/** A sync run tagged with the dealership it belongs to. */
export interface RunWithDealer extends SyncRun {
  dealer_name: string;
  dealer_active: boolean;
}

/** A daily snapshot tagged with its dealership. */
export interface SnapshotWithDealer extends Snapshot {
  dealer_id: number;
  dealer_name: string;
}

/** Background refetch interval from user settings; `false` disables polling. */
function useRefetchInterval(): number | false {
  const { settings } = useAppSettings();
  return settings.autoRefreshMs > 0 ? settings.autoRefreshMs : false;
}

/** Shared dealer list — one cache entry reused by every page and the palette. */
export function useDealerList() {
  const refetchInterval = useRefetchInterval();
  return useQuery({
    queryKey: ['dealers'],
    queryFn: () => listDealers(),
    refetchInterval,
  });
}

export function useDashboardSummary() {
  const refetchInterval = useRefetchInterval();
  return useQuery({ queryKey: ['dashboard', 'summary'], queryFn: getSummary, refetchInterval });
}

export function useDealerStats() {
  const refetchInterval = useRefetchInterval();
  return useQuery({ queryKey: ['dashboard', 'dealers'], queryFn: getDealerStats, refetchInterval });
}

/**
 * Fan out the per-dealer runs endpoint and flatten the result into one feed.
 *
 * There is no aggregate runs endpoint, so this is the only way to build a
 * cross-dealer job view. Each dealer keeps its own cache entry, which means a
 * single dealer's sync invalidates just that slice rather than the whole feed.
 */
export function useAllRuns(
  dealers: readonly Dealer[] | undefined,
  limit = 10,
  enabled = true,
  /** Poll interval while ANY run in that dealer's page is still `running`. */
  livePollMs: number | false = false,
) {
  const list = useMemo(() => dealers ?? [], [dealers]);

  const combine = useCallback(
    (results: { data?: SyncRun[]; isPending: boolean; isFetching: boolean; isError: boolean }[]) => {
      const runs: RunWithDealer[] = [];
      results.forEach((res, i) => {
        const dealer = list[i];
        if (!dealer || !res.data) return;
        res.data.forEach((run) =>
          runs.push({ ...run, dealer_name: dealer.name, dealer_active: dealer.active }),
        );
      });
      runs.sort((a, b) => new Date(b.started_at).getTime() - new Date(a.started_at).getTime());
      return {
        runs,
        isLoading: results.length > 0 && results.some((r) => r.isPending),
        isFetching: results.some((r) => r.isFetching),
        isError: results.length > 0 && results.every((r) => r.isError),
      };
    },
    [list],
  );

  return useQueries({
    queries: list.map((d) => ({
      queryKey: ['dealer', d.id, 'runs', limit] as const,
      queryFn: () => listDealerRuns(d.id, limit),
      enabled,
      staleTime: 15_000,
      refetchInterval: livePollMs
        ? (query: { state: { data?: SyncRun[] } }) =>
            query.state.data?.some((r) => r.status === 'running') ? livePollMs : false
        : undefined,
    })),
    combine,
  });
}

/**
 * Fan out the 30-day snapshot endpoint across dealers to build platform-wide
 * inventory trends. Snapshots are written once per day, so they cache well.
 */
export function useAllTrends(dealers: readonly Dealer[] | undefined, days = 30, enabled = true) {
  const list = useMemo(() => dealers ?? [], [dealers]);

  const combine = useCallback(
    (results: { data?: Snapshot[]; isPending: boolean; isFetching: boolean }[]) => {
      const snapshots: SnapshotWithDealer[] = [];
      results.forEach((res, i) => {
        const dealer = list[i];
        if (!dealer || !res.data) return;
        res.data.forEach((snap) =>
          snapshots.push({ ...snap, dealer_id: dealer.id, dealer_name: dealer.name }),
        );
      });
      return {
        snapshots,
        isLoading: results.length > 0 && results.some((r) => r.isPending),
        isFetching: results.some((r) => r.isFetching),
      };
    },
    [list],
  );

  return useQueries({
    queries: list.map((d) => ({
      queryKey: ['dashboard', 'trend', d.id, days] as const,
      queryFn: () => getDealerTrend(d.id, days),
      enabled,
      staleTime: 5 * 60_000,
    })),
    combine,
  });
}

/**
 * Collapse per-dealer snapshots into one series per calendar date.
 * Dealers are only counted on dates they actually reported, so a dealer added
 * mid-window doesn't create a phantom dip in the totals.
 */
export interface DailyTotals {
  date: string;
  total: number;
  added: number;
  removed: number;
  sold: number;
  priceChanged: number;
  dealers: number;
}

export function aggregateByDate(snapshots: readonly SnapshotWithDealer[]): DailyTotals[] {
  const byDate = new Map<string, DailyTotals>();
  for (const s of snapshots) {
    const entry = byDate.get(s.snapshot_date) ?? {
      date: s.snapshot_date,
      total: 0,
      added: 0,
      removed: 0,
      sold: 0,
      priceChanged: 0,
      dealers: 0,
    };
    entry.total += s.total_active;
    entry.added += s.added;
    entry.removed += s.removed;
    entry.sold += s.sold;
    entry.priceChanged += s.price_changed;
    entry.dealers += 1;
    byDate.set(s.snapshot_date, entry);
  }
  return [...byDate.values()].sort((a, b) => a.date.localeCompare(b.date));
}

/** Share of the day's inventory movement that came from each dealership. */
export function topMovers(stats: readonly DealerStats[] | undefined, limit = 5) {
  return [...(stats ?? [])]
    .map((s) => ({ ...s, delta: s.total_today - s.total_yesterday }))
    .filter((s) => s.delta !== 0)
    .sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta))
    .slice(0, limit);
}
