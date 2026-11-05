import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useSnackbar } from 'notistack';
import { syncAllDealers } from '../api/dealers';
import { getErrorMessage } from '../api/client';

/** The topbar's "Run Scraper" action: the server starts every active dealer. */
export function useRunScraper() {
  const qc = useQueryClient();
  const { enqueueSnackbar } = useSnackbar();

  return useMutation({
    mutationFn: syncAllDealers,
    onSuccess: ({ queued, already_running, skipped_no_settings }) => {
      const parts = [
        queued > 0 ? `${queued.toLocaleString()} sync${queued === 1 ? '' : 's'} started` : null,
        already_running > 0 ? `${already_running.toLocaleString()} already running` : null,
        skipped_no_settings > 0 ? `${skipped_no_settings.toLocaleString()} skipped, no settings` : null,
      ].filter(Boolean);

      enqueueSnackbar(parts.join(' · ') || 'Nothing to sync', {
        variant: skipped_no_settings > 0 ? 'warning' : queued > 0 ? 'success' : 'info',
      });

      qc.invalidateQueries({ queryKey: ['dealers'] });
      qc.invalidateQueries({ queryKey: ['dashboard'] });
      qc.invalidateQueries({ queryKey: ['vehicles'] });
    },
    onError: (e) => enqueueSnackbar(getErrorMessage(e), { variant: 'error' }),
  });
}
