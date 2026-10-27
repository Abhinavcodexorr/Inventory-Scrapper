import { api } from './client';
import type { ChangeList, ChangeSummary, ChangeListParams } from '../types';

/**
 * Fleet-wide change feed. Answers "which vehicles changed?", where the
 * dashboard only answers "how many?" — the audit log is per-vehicle, so
 * without this an operator would have to know a car id before they could
 * look anything up.
 */
export async function listChanges(params?: ChangeListParams): Promise<ChangeList> {
  const { data } = await api.get<ChangeList>('/api/changes', { params });
  return data;
}

export async function getChangeSummary(
  params?: Pick<ChangeListParams, 'hours' | 'dealer_id'>,
): Promise<ChangeSummary> {
  const { data } = await api.get<ChangeSummary>('/api/changes/summary', { params });
  return data;
}
