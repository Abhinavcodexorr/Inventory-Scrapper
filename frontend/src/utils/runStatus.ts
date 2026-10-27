/**
 * Scrape-run status vocabulary.
 *
 * The backend uses TWO different words for a healthy run, and confusing them
 * is why the Jobs page showed "0 successful runs / 0% success rate" while the
 * table beneath it listed four completed runs:
 *
 *   ScrapeRun.status        -> running | completed | incomplete | failed | cancelled
 *   Dealer.last_sync_status -> success | incomplete | failed | cancelled
 *
 * Five pages compared a RUN's status against 'success', which a run is never
 * set to, so every success counter and success-rate read zero while the run
 * tables beside them rendered "Completed" correctly. Route such checks through
 * here rather than comparing strings at the call site.
 *
 * `incomplete` is deliberately neither success nor failure: the scrape ran but
 * the completeness gate refused to trust the result, so nothing was marked
 * removed. Counting it as either hides the one state that needs a human.
 */
export function isRunSuccess(status: string | null | undefined): boolean {
  // 'success' is accepted too, so the same helper can classify a dealer's
  // last_sync_status without the caller knowing which field it came from.
  return status === 'completed' || status === 'success';
}
