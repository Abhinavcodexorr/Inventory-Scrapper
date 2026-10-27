import { api } from "./client";
import type {
  Dealer,
  DealerCreate,
  DealerUpdate,
  Discovery,
  DiscoveryJob,
  DiscoveryRunStatus,
  SiteCompatibilityReport,
  SyncCancel,
  SyncJob,
  SyncJobStatus,
  SyncRun,
} from "../types";

const DISCOVERY_POLL_MS = 2000;
/** Discovery server budget is 300s; allow headroom for queue + finalize. */
const DISCOVERY_GIVE_UP_MS = 600_000;

export interface DiscoveryProgress {
  runId: number;
  status: string;
  phase: string | null;
  phasesDone: number;
  phasesTotal: number;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Fleet-wide site compatibility: which dealer websites the engine can scrape,
 * and which need chargeable custom development.
 */
export async function getCompatibilityReport(
  billableOnly = false,
): Promise<SiteCompatibilityReport> {
  const { data } = await api.get<SiteCompatibilityReport>('/api/dealers/compatibility', {
    params: billableOnly ? { billable_only: true } : undefined,
  });
  return data;
}

/**
 * Auto-derive a scrape_config from just the dealer's website URL.
 *
 * Dispatches a background discovery job (POST /discover) and polls
 * GET /discover/runs/{run_id} until completion. Supports cancellation
 * and phase-by-phase progress — no blocking ?sync=true call.
 */
export async function discoverConfig(
  base_url: string,
  onProgress?: (progress: DiscoveryProgress) => void,
): Promise<Discovery> {
  const { data: job } = await api.post<DiscoveryJob>("/api/dealers/discover", {
    base_url,
  });

  const runId = job.run_id;
  const terminal = new Set(["completed", "failed", "cancelled"]);
  const started = Date.now();

  while (true) {
    const { data: status } = await api.get<DiscoveryRunStatus>(
      `/api/dealers/discover/runs/${runId}`,
    );

    onProgress?.({
      runId,
      status: status.status,
      phase: status.phase,
      phasesDone: status.phases_done,
      phasesTotal: status.phases_total,
    });

    if (terminal.has(status.status)) {
      const { data: final } = await api.get<DiscoveryRunStatus>(
        `/api/dealers/discover/runs/${runId}`,
        { params: { include_report: true } },
      );
      if (final.status === "cancelled" && !final.result) {
        throw new Error("Discovery was cancelled");
      }
      if (final.result) {
        return {
          ...final.result,
          run_id: runId,
          report_available: final.report != null,
          report: final.report ?? undefined,
        };
      }
      throw new Error(final.error || `Discovery ${final.status}`);
    }

    if (Date.now() - started > DISCOVERY_GIVE_UP_MS) {
      throw new Error("Discovery timed out waiting for completion");
    }

    await sleep(DISCOVERY_POLL_MS);
  }
}

/** Ask a running discovery job to stop cooperatively. */
export async function cancelDiscoveryRun(runId: number, jobId?: string) {
  const { data } = await api.post<{ status: string; detail: string }>(
    `/api/dealers/discover/runs/${runId}/cancel`,
    null,
    { params: jobId ? { job_id: jobId } : undefined },
  );
  return data;
}

export interface DealerListParams {
  active?: boolean;
  provider?: string;
  q?: string;
}

export async function listDealers(
  params?: DealerListParams,
): Promise<Dealer[]> {
  const { data } = await api.get<Dealer[]>("/api/dealers", { params });
  return data;
}

export async function getDealer(id: number): Promise<Dealer> {
  const { data } = await api.get<Dealer>(`/api/dealers/${id}`);
  return data;
}

export async function createDealer(payload: DealerCreate): Promise<Dealer> {
  const { data } = await api.post<Dealer>("/api/dealers", payload);
  return data;
}

export async function updateDealer(
  id: number,
  payload: DealerUpdate,
): Promise<Dealer> {
  const { data } = await api.patch<Dealer>(`/api/dealers/${id}`, payload);
  return data;
}

/**
 * Delete a dealership.
 * Without `purge` the API returns 409 when the dealer still holds inventory —
 * history is meant to be retained, so removal must be deliberate.
 * `purge: true` also deletes its vehicles, price history, audit logs and runs.
 */
export async function deleteDealer(id: number, purge = false): Promise<void> {
  await api.delete(`/api/dealers/${id}`, {
    params: purge ? { purge: true } : undefined,
  });
}

export async function activateDealer(id: number): Promise<Dealer> {
  const { data } = await api.post<Dealer>(`/api/dealers/${id}/activate`);
  return data;
}

export async function deactivateDealer(id: number): Promise<Dealer> {
  const { data } = await api.post<Dealer>(`/api/dealers/${id}/deactivate`);
  return data;
}

export async function syncDealer(id: number): Promise<SyncJob> {
  const { data } = await api.post<SyncJob>(`/api/dealers/${id}/sync`);
  return data;
}

/** Ask a running scrape to stop. Cooperative: it stops at its next checkpoint
 *  and keeps whatever it already scraped. */
export async function cancelSync(
  id: number,
  jobId?: string,
): Promise<SyncCancel> {
  const { data } = await api.post<SyncCancel>(
    `/api/dealers/${id}/sync/cancel`,
    null,
    {
      params: jobId ? { job_id: jobId } : undefined,
    },
  );
  return data;
}

export async function getSyncJobStatus(
  id: number,
  jobId: string,
): Promise<SyncJobStatus> {
  const { data } = await api.get<SyncJobStatus>(
    `/api/dealers/${id}/sync/${jobId}`,
  );
  return data;
}

/** Live sync state without a job id — survives a page reload mid-sync. */
export async function getCurrentSyncStatus(id: number): Promise<SyncJobStatus> {
  const { data } = await api.get<SyncJobStatus>(
    `/api/dealers/${id}/sync/current`,
  );
  return data;
}

export async function listDealerRuns(
  id: number,
  limit = 20,
): Promise<SyncRun[]> {
  const { data } = await api.get<SyncRun[]>(`/api/dealers/${id}/runs`, {
    params: { limit },
  });
  return data;
}

export async function listProviders(): Promise<string[]> {
  const { data } = await api.get<string[]>("/api/dealers/providers/list");
  return data;
}
