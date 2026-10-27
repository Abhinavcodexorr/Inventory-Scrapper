import type { SyncRun } from '../types';

/**
 * Plain-language description of one scrape run.
 *
 * "scraped_count" is the number of ROWS the dealer's feed returned, including
 * ones the engine ignores on purpose (Archive, Unpublished). Showing it bare
 * next to a website that lists 104 vehicles read as "1,090 scraped, 10x too
 * many" when 986 of those rows were correctly skipped. Every place that shows a
 * run goes through here so the wording cannot drift apart.
 */
type RunLike = Pick<
  SyncRun,
  | 'status'
  | 'scraped_count'
  | 'reported_total'
  | 'new_count'
  | 'removed_count'
  | 'sold_count'
  | 'price_changed_count'
  | 'stats'
>;

export interface RunSummary {
  /** Vehicles the dealer's site actually lists: feed rows minus ignored ones. */
  listed: number;
  /** Feed rows skipped on purpose (archived / unpublished). */
  ignored: number;
  /** One line: what this run saw. */
  headline: string;
  /** What changed in the inventory because of this run. */
  changes: string[];
  /** headline and changes joined for single-line display. */
  text: string;
}

export function ignoredRows(run: Pick<SyncRun, 'stats'>): number {
  const n = run.stats?.counters?.hidden;
  return typeof n === 'number' && n > 0 ? n : 0;
}

export function summarizeRun(run: RunLike): RunSummary {
  const ignored = ignoredRows(run);
  const listed = Math.max(run.scraped_count - ignored, 0);
  const n = (v: number) => v.toLocaleString();

  let headline: string;
  if (run.status === 'incomplete' && run.reported_total) {
    headline = `Incomplete — read ${n(listed)} of ${n(run.reported_total)} vehicles`;
  } else if (ignored > 0) {
    headline = `${n(listed)} vehicles listed · ${n(ignored)} archived/unpublished rows ignored`;
  } else {
    headline = `${n(listed)} vehicles`;
  }

  const changes = [
    run.new_count > 0 ? `${n(run.new_count)} new` : null,
    run.removed_count > 0 ? `${n(run.removed_count)} removed` : null,
    run.sold_count > 0 ? `${n(run.sold_count)} marked sold` : null,
    run.price_changed_count > 0 ? `${n(run.price_changed_count)} repriced` : null,
  ].filter((c): c is string => c !== null);

  // An incomplete run never removes anything (the completeness gate refuses to
  // trust a partial read) — say so, because that is what the reader worries about.
  if (run.status === 'incomplete' && run.removed_count === 0) {
    changes.push('nothing removed');
  }

  return { listed, ignored, headline, changes, text: [headline, ...changes].join(' · ') };
}
