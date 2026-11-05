import { useMemo } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import {
  getLatestRunStatuses,
  getRunStats,
  listDealers,
  listDealersPage,
  listRecentRuns,
  type DealerListParams,
} from '../api/dealers';
import {
  getDealerStats,
  getFleetTrend,
  getInventoryShare,
  getSummary,
  type DealerStatsParams,
} from '../api/dashboard';
import { useAppSettings } from '../theme/settings';
import type { DealerStats } from '../types';

function useRefetchInterval(): number | false {
  const { settings } = useAppSettings();
  return settings.autoRefreshMs > 0 ? settings.autoRefreshMs : false;
}

// The fleet can hold tens of thousands of dealers, so nothing here loads them
// all: the server pages, searches and totals, and the browser holds one page.
// Query keys start with 'dealers' or 'dashboard' so the existing invalidations
// after a sync, edit or delete reach them.

/** One page of dealers. The previous page stays on screen while the next loads. */
export function useDealerPage(params: DealerListParams) {
  const refetchInterval = useRefetchInterval();
  return useQuery({
    queryKey: ['dealers', 'page', params],
    queryFn: () => listDealersPage(params),
    placeholderData: keepPreviousData,
    refetchInterval,
  });
}

/** Names for the dealers currently on screen. */
export function useDealerNames(ids: readonly number[]) {
  const unique = useMemo(() => [...new Set(ids)].sort((a, b) => a - b), [ids]);
  const namesQ = useQuery({
    queryKey: ['dealers', 'names', unique],
    queryFn: () => listDealers({ ids: unique.join(','), limit: 500 }),
    enabled: unique.length > 0,
    placeholderData: keepPreviousData,
  });
  return useMemo(() => {
    const names = new Map((namesQ.data ?? []).map((d) => [d.id, d.name]));
    return (id: number) => names.get(id) ?? `Dealer #${id}`;
  }, [namesQ.data]);
}

export function useDashboardSummary() {
  const refetchInterval = useRefetchInterval();
  return useQuery({ queryKey: ['dashboard', 'summary'], queryFn: getSummary, refetchInterval });
}

export function useDealerStats(params?: DealerStatsParams) {
  const refetchInterval = useRefetchInterval();
  return useQuery({
    queryKey: ['dashboard', 'dealers', params],
    queryFn: () => getDealerStats(params),
    refetchInterval,
  });
}

export function useInventoryShare(top: number) {
  return useQuery({ queryKey: ['dashboard', 'share', top], queryFn: () => getInventoryShare(top) });
}

interface RecentRunsOptions {
  limit?: number;
  offset?: number;
  status?: string;
  dealerId?: number;
  q?: string;
  /** Poll interval while any listed run is still `running`. */
  livePollMs?: number;
}

/** The newest scrape runs across every dealer, or one. */
export function useRecentRuns({ limit = 50, offset = 0, status, dealerId, q, livePollMs }: RecentRunsOptions = {}) {
  const query = useQuery({
    queryKey: ['dealers', 'runs', { limit, offset, status, dealerId, q }],
    queryFn: () => listRecentRuns({ limit, offset, status, dealer_id: dealerId, q }),
    placeholderData: keepPreviousData,
    staleTime: 15_000,
    refetchInterval: livePollMs
      ? (q2) => (q2.state.data?.runs.some((r) => r.status === 'running') ? livePollMs : false)
      : undefined,
  });
  return {
    runs: query.data?.runs ?? [],
    total: query.data?.total ?? 0,
    isLoading: query.isLoading,
    isFetching: query.isFetching,
    isError: query.isError,
  };
}

export function useRunStats(refetchInterval?: number) {
  return useQuery({ queryKey: ['dealers', 'run-stats'], queryFn: getRunStats, refetchInterval });
}

/** Success tally per dealer for the dealers on screen, from their newest runs. */
export function useSuccessTally(ids: readonly number[], isSuccess: (status: string) => boolean) {
  const statusesQ = useQuery({
    queryKey: ['dealers', 'latest-statuses', ids],
    queryFn: () => getLatestRunStatuses([...ids]),
    enabled: ids.length > 0,
    placeholderData: keepPreviousData,
    staleTime: 15_000,
  });
  return useMemo(() => {
    const tally = new Map<number, { ok: number; total: number }>();
    for (const [id, statuses] of Object.entries(statusesQ.data ?? {})) {
      const finished = statuses.filter((s) => s !== 'running');
      tally.set(Number(id), { ok: finished.filter(isSuccess).length, total: finished.length });
    }
    return tally;
  }, [statusesQ.data, isSuccess]);
}

/** Daily inventory totals across every dealer, or one. */
export function useFleetTrend(days: number, dealerId?: number) {
  const query = useQuery({
    queryKey: ['dashboard', 'trend', days, dealerId],
    queryFn: () => getFleetTrend(days, dealerId),
    staleTime: 5 * 60_000,
  });
  return { daily: query.data ?? [], isLoading: query.isLoading };
}

/** Share of the day's inventory movement that came from each dealership. */
export function topMovers(stats: readonly DealerStats[] | undefined, limit = 5) {
  return [...(stats ?? [])]
    .map((s) => ({ ...s, delta: s.total_today - s.total_yesterday }))
    .filter((s) => s.delta !== 0)
    .sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta))
    .slice(0, limit);
}
