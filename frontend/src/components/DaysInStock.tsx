import { Box, Typography } from '@mui/material';
import { formatDateShort } from '../utils/format';
import type { Vehicle } from '../types';

/**
 * Days in Stock for one vehicle, as the dealer's own website reports it.
 *
 * The number comes from the API (stock date to today, frozen at the sold/removed
 * date), never from the browser's clock. A dash means the website does not say,
 * which is different from 0 (listed today), so the two are never mixed up.
 */
export function DaysInStock({ vehicle: v }: { vehicle: Pick<Vehicle, 'days_in_stock' | 'stock_date' | 'status'> }) {
  if (v.days_in_stock === null || v.days_in_stock === undefined) {
    return (
      <Typography variant="body2" color="text.disabled">
        —
      </Typography>
    );
  }
  return (
    <Box sx={{ lineHeight: 1.25 }}>
      <Typography variant="body2" sx={{ fontVariantNumeric: 'tabular-nums', fontWeight: 600 }}>
        {v.days_in_stock.toLocaleString()}
      </Typography>
      {v.stock_date && (
        <Typography variant="caption" color="text.secondary" noWrap sx={{ display: 'block' }}>
          Added since {formatDateShort(v.stock_date)}
        </Typography>
      )}
    </Box>
  );
}
