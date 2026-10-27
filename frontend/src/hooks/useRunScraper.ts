import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useSnackbar } from 'notistack';
import { syncDealer } from '../api/dealers';
import { getErrorMessage } from '../api/client';
import type { Dealer } from '../types';

export interface RunScraperResult {
  started: number;
  alreadyRunning: number;
  failed: number;
}

/**
 * The topbar's "Run Scraper" action: dispatches a sync for every active dealer.
 *
 * There is no bulk endpoint, so this fans out over `POST /api/dealers/{id}/sync`
 * and reports one summary rather than a toast per dealer. `allSettled` means a
 * single unreachable dealer never aborts the rest of the run.
 */
export function useRunScraper() {
  const qc = useQueryClient();
  const { enqueueSnackbar } = useSnackbar();

  return useMutation({
    mutationFn: async (dealers: readonly Dealer[]): Promise<RunScraperResult> => {
      const targets = dealers.filter((d) => d.active);
      if (targets.length === 0) throw new Error('No active dealerships to sync');

      const results = await Promise.allSettled(targets.map((d) => syncDealer(d.id)));

      let started = 0;
      let alreadyRunning = 0;
      let failed = 0;
      for (const r of results) {
        if (r.status === 'rejected') failed += 1;
        else if (r.value.status === 'running') alreadyRunning += 1;
        else started += 1;
      }
      return { started, alreadyRunning, failed };
    },
    onSuccess: ({ started, alreadyRunning, failed }) => {
      const parts = [
        started > 0 ? `${started} sync${started === 1 ? '' : 's'} started` : null,
        alreadyRunning > 0 ? `${alreadyRunning} already running` : null,
        failed > 0 ? `${failed} failed to queue` : null,
      ].filter(Boolean);

      enqueueSnackbar(parts.join(' · ') || 'Nothing to sync', {
        variant: failed > 0 ? 'warning' : started > 0 ? 'success' : 'info',
      });

      qc.invalidateQueries({ queryKey: ['dealers'] });
      qc.invalidateQueries({ queryKey: ['dashboard'] });
      qc.invalidateQueries({ queryKey: ['vehicles'] });
    },
    onError: (e) => enqueueSnackbar(getErrorMessage(e), { variant: 'error' }),
  });
}
