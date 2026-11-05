import { api } from './client';
import type { DailyTotals, DashboardSummary, DealerStats, InventoryShare, Snapshot } from '../types';

export async function getSummary(): Promise<DashboardSummary> {
  const { data } = await api.get<DashboardSummary>('/api/dashboard/summary');
  return data;
}

export interface DealerStatsParams {
  /** Largest dealers first. */
  limit?: number;
  /** Only failed, never-synced or sharply shrunk dealers. */
  attention?: boolean;
}

export async function getDealerStats(params?: DealerStatsParams): Promise<DealerStats[]> {
  const { data } = await api.get<DealerStats[]>('/api/dashboard/dealers', { params });
  return data;
}

export async function getFleetTrend(days: number, dealerId?: number): Promise<DailyTotals[]> {
  const { data } = await api.get<(Omit<DailyTotals, 'priceChanged'> & { price_changed: number })[]>(
    '/api/dashboard/trend',
    { params: { days, dealer_id: dealerId } },
  );
  return data.map(({ price_changed, ...day }) => ({ ...day, priceChanged: price_changed }));
}

export async function getInventoryShare(top: number): Promise<InventoryShare> {
  const { data } = await api.get<InventoryShare>('/api/dashboard/inventory-share', { params: { top } });
  return data;
}

export async function getDealerTrend(dealerId: number, days = 30): Promise<Snapshot[]> {
  const { data } = await api.get<Snapshot[]>(`/api/dashboard/dealers/${dealerId}/trend`, {
    params: { days },
  });
  return data;
}
