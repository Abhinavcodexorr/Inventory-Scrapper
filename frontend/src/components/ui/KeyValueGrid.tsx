import type { ReactNode } from 'react';
import { Box, Typography } from '@mui/material';

export interface KeyValueItem {
  label: string;
  value: ReactNode;
  /** Renders muted + italic — for values the scraper did not capture. */
  muted?: boolean;
  /** Force this item onto its own full-width row (long URLs, addresses). */
  wide?: boolean;
}

interface Props {
  items: KeyValueItem[];
  /** Columns at the widest breakpoint. */
  columns?: 2 | 3 | 4;
}

/** Responsive label/value grid used for specs, dealer details and metadata. */
export function KeyValueGrid({ items, columns = 4 }: Props) {
  return (
    <Box
      sx={{
        display: 'grid',
        gridTemplateColumns: {
          xs: 'repeat(2, minmax(0, 1fr))',
          sm: `repeat(${Math.min(columns, 2)}, minmax(0, 1fr))`,
          md: `repeat(${Math.min(columns, 3)}, minmax(0, 1fr))`,
          lg: `repeat(${columns}, minmax(0, 1fr))`,
        },
        gap: 2.5,
      }}
    >
      {items.map((item) => (
        <Box key={item.label} sx={{ minWidth: 0, gridColumn: item.wide ? '1 / -1' : undefined }}>
          <Typography
            variant="caption"
            sx={{ display: 'block', color: 'text.secondary', fontWeight: 560, mb: 0.25 }}
          >
            {item.label}
          </Typography>
          <Box
            sx={{
              fontSize: '0.9375rem',
              lineHeight: 1.5,
              color: item.muted ? 'text.disabled' : 'text.primary',
              fontStyle: item.muted ? 'italic' : 'normal',
              wordBreak: 'break-word',
            }}
          >
            {item.value}
          </Box>
        </Box>
      ))}
    </Box>
  );
}
