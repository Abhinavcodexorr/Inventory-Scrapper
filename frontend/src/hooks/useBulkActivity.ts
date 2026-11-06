import { useQuery } from '@tanstack/react-query';
import { getBulkActivity } from '../api/bulkImports';

/** Websites being checked and waiting across every bulk list. Polls fast while
 * something is running, slowly otherwise. */
export function useBulkActivity() {
  return useQuery({
    queryKey: ['bulk-imports', 'activity'],
    queryFn: getBulkActivity,
    refetchInterval: (q) => (q.state.data?.running_imports ? 5_000 : 30_000),
  });
}
