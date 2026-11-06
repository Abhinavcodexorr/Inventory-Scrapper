import { api } from './client';
import type { BulkImport, BulkImportRowResult, BulkPreview, BulkRow } from '../types';

export interface BulkImportPayload {
  provider: string | null;
  filename: string | null;
  rows: BulkRow[];
}

export async function previewBulkImport(payload: BulkImportPayload): Promise<BulkPreview> {
  const { data } = await api.post<BulkPreview>('/api/bulk-imports/preview', payload);
  return data;
}

export async function createBulkImport(payload: BulkImportPayload): Promise<BulkImport> {
  const { data } = await api.post<BulkImport>('/api/bulk-imports', payload);
  return data;
}

export async function listBulkImports(limit = 20): Promise<BulkImport[]> {
  const { data } = await api.get<BulkImport[]>('/api/bulk-imports', { params: { limit } });
  return data;
}

export async function getBulkImport(id: number): Promise<BulkImport> {
  const { data } = await api.get<BulkImport>(`/api/bulk-imports/${id}`);
  return data;
}

export async function listBulkImportRows(
  id: number,
  params: { status?: string; limit: number; offset?: number },
): Promise<{ rows: BulkImportRowResult[]; total: number }> {
  const res = await api.get<BulkImportRowResult[]>(`/api/bulk-imports/${id}/rows`, { params });
  return { rows: res.data, total: Number(res.headers['x-total-count'] ?? res.data.length) };
}

export async function stopBulkImport(id: number): Promise<BulkImport> {
  const { data } = await api.post<BulkImport>(`/api/bulk-imports/${id}/stop`);
  return data;
}

export async function retryBulkImport(id: number): Promise<BulkImport> {
  const { data } = await api.post<BulkImport>(`/api/bulk-imports/${id}/retry`);
  return data;
}

const SEEN_KEY = 'bulk-imports-seen';

function readSeen(): number[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(SEEN_KEY) ?? '[]');
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

/** Finished imports whose report this browser has not opened yet. */
export function isBulkReportSeen(id: number): boolean {
  return readSeen().includes(id);
}

export function markBulkReportSeen(id: number): void {
  try {
    const seen = readSeen();
    if (!seen.includes(id)) localStorage.setItem(SEEN_KEY, JSON.stringify([...seen, id].slice(-100)));
  } catch {
    // Without storage the notification simply stays until it ages out.
  }
}
