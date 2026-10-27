import { api } from './client';
import type { DashboardSummary, DealerStats, Snapshot } from '../types';

export async function getSummary(): Promise<DashboardSummary> {
  const { data } = await api.get<DashboardSummary>('/api/dashboard/summary');
  return data;
}

export async function getDealerStats(): Promise<DealerStats[]> {
  const { data } = await api.get<DealerStats[]>('/api/dashboard/dealers');
  return data;
}

export async function getDealerTrend(dealerId: number, days = 30): Promise<Snapshot[]> {
  const { data } = await api.get<Snapshot[]>(`/api/dashboard/dealers/${dealerId}/trend`, {
    params: { days },
  });
  return data;
}
