import { useMemo } from 'react';
import { Box, Table, TableBody, TableCell, TableHead, TableRow, Typography } from '@mui/material';
import { SectionCard } from './ui';
import type { DealerStats } from '../types';

/**
 * Per-dealer count of every row the dealer's feed returned, by the feed's own
 * status word, so a dealer's number can be matched against its website by hand.
 *
 *   Listed on website = In stock + Sold
 *
 * That holds for feeds like Zop's, where the dealer's page shows sold vehicles
 * with a badge and omits Archive/Unpublished (Eco Cars 59 + 45 = 104; Cardora
 * 172 + 520 = 692 before duplicate VINs; GR Cars 46 + 80 = 126).
 */
const KNOWN = ['instock', 'sold', 'archive', 'unpublished'] as const;

interface Row {
  id: number;
  name: string;
  instock: number;
  sold: number;
  archive: number;
  unpublished: number;
  other: number;
  total: number;
  partial: boolean;
}

function toRow(s: DealerStats): Row | null {
  if (!s.feed_statuses) return null;
  const f = s.feed_statuses;
  const total = s.feed_total ?? Object.values(f).reduce((a, b) => a + b, 0);
  const known = KNOWN.reduce((a, k) => a + (f[k] ?? 0), 0);
  return {
    id: s.dealer_id,
    name: s.dealer_name,
    instock: f.instock ?? 0,
    sold: f.sold ?? 0,
    archive: f.archive ?? 0,
    unpublished: f.unpublished ?? 0,
    other: Math.max(total - known, 0),
    total,
    partial: s.feed_run_status === 'incomplete',
  };
}

const n = (v: number) => v.toLocaleString();

export function FeedReconciliationCard({ stats }: { stats: DealerStats[] }) {
  const rows = useMemo(
    () => stats.map(toRow).filter((r): r is Row => r !== null).sort((a, b) => a.name.localeCompare(b.name)),
    [stats],
  );
  const sum = useMemo(
    () =>
      rows.reduce(
        (a, r) => ({
          instock: a.instock + r.instock,
          sold: a.sold + r.sold,
          archive: a.archive + r.archive,
          unpublished: a.unpublished + r.unpublished,
          other: a.other + r.other,
          total: a.total + r.total,
        }),
        { instock: 0, sold: 0, archive: 0, unpublished: 0, other: 0, total: 0 },
      ),
    [rows],
  );

  if (rows.length === 0) return null;

  const num = { textAlign: 'right' as const, fontVariantNumeric: 'tabular-nums' };
  const head = { fontWeight: 600, whiteSpace: 'nowrap' as const };

  return (
    <SectionCard
      title="Feed reconciliation"
      description="Every row each dealer's feed returned in its latest run, by status. Listed on website = In stock + Sold."
      dense
      flush
      sx={{ mt: 3 }}
    >
      <Box sx={{ overflowX: 'auto' }}>
        <Table size="small" sx={{ minWidth: 640 }}>
          <TableHead>
            <TableRow>
              <TableCell sx={head}>Dealer</TableCell>
              <TableCell sx={{ ...head, ...num }}>In stock</TableCell>
              <TableCell sx={{ ...head, ...num }}>Sold</TableCell>
              <TableCell sx={{ ...head, ...num }}>Archive</TableCell>
              <TableCell sx={{ ...head, ...num }}>Unpublished</TableCell>
              {sum.other > 0 && <TableCell sx={{ ...head, ...num }}>Other</TableCell>}
              <TableCell sx={{ ...head, ...num }}>Total rows</TableCell>
              <TableCell sx={{ ...head, ...num }}>Listed on website</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.map((r) => (
              <TableRow key={r.id} hover>
                <TableCell>
                  {r.name}
                  {r.partial && (
                    <Typography component="span" variant="caption" color="warning.main" sx={{ ml: 1 }}>
                      partial read
                    </Typography>
                  )}
                </TableCell>
                <TableCell sx={num}>{n(r.instock)}</TableCell>
                <TableCell sx={num}>{n(r.sold)}</TableCell>
                <TableCell sx={num}>{n(r.archive)}</TableCell>
                <TableCell sx={num}>{n(r.unpublished)}</TableCell>
                {sum.other > 0 && <TableCell sx={num}>{n(r.other)}</TableCell>}
                <TableCell sx={num}>{n(r.total)}</TableCell>
                <TableCell sx={{ ...num, fontWeight: 600 }}>{n(r.instock + r.sold)}</TableCell>
              </TableRow>
            ))}
            <TableRow sx={{ '& td': { fontWeight: 700, borderTop: 2, borderColor: 'divider' } }}>
              <TableCell>All dealers</TableCell>
              <TableCell sx={num}>{n(sum.instock)}</TableCell>
              <TableCell sx={num}>{n(sum.sold)}</TableCell>
              <TableCell sx={num}>{n(sum.archive)}</TableCell>
              <TableCell sx={num}>{n(sum.unpublished)}</TableCell>
              {sum.other > 0 && <TableCell sx={num}>{n(sum.other)}</TableCell>}
              <TableCell sx={num}>{n(sum.total)}</TableCell>
              <TableCell sx={num}>{n(sum.instock + sum.sold)}</TableCell>
            </TableRow>
          </TableBody>
        </Table>
      </Box>
    </SectionCard>
  );
}
