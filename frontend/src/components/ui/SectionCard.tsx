import type { ReactNode } from 'react';
import { Box, Typography, type SxProps, type Theme } from '@mui/material';
import { Surface } from './Surface';
import { useTokens } from '../../theme/useTokens';

interface Props {
  title?: ReactNode;
  description?: ReactNode;
  /** Right-aligned controls in the header (filters, links, menus). */
  actions?: ReactNode;
  /** Small icon or badge to the left of the title. */
  adornment?: ReactNode;
  children: ReactNode;
  /** Remove body padding — use when the body is a table that bleeds to the edge. */
  flush?: boolean;
  /** Footer strip below the body, separated by a hairline. */
  footer?: ReactNode;
  /** Tighter padding and smaller title — for compact layouts like the dashboard. */
  dense?: boolean;
  sx?: SxProps<Theme>;
}

/** Titled panel: the standard container for a chart, table or grouped fields. */
export function SectionCard({
  title,
  description,
  actions,
  adornment,
  children,
  flush = false,
  footer,
  dense = false,
  sx,
}: Props) {
  const tk = useTokens();
  const hasHeader = Boolean(title || description || actions || adornment);
  const pad = dense ? 2 : 2.5;

  return (
    <Surface sx={[{ display: 'flex', flexDirection: 'column', overflow: 'hidden' }, ...(Array.isArray(sx) ? sx : [sx])]}>
      {hasHeader && (
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 2,
            px: pad,
            py: dense ? 1.5 : 2,
            flexWrap: { xs: 'wrap', sm: 'nowrap' },
            borderBottom: flush ? `1px solid ${tk.surfaces.border}` : 'none',
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, minWidth: 0 }}>
            {adornment}
            <Box sx={{ minWidth: 0 }}>
              {title && (
                <Typography variant={dense ? 'subtitle1' : 'h6'} sx={{ lineHeight: 1.35 }} noWrap>
                  {title}
                </Typography>
              )}
              {description && (
                <Typography variant="body2" color="text.secondary" sx={{ mt: 0.25 }}>
                  {description}
                </Typography>
              )}
            </Box>
          </Box>
          {actions && (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexShrink: 0 }}>{actions}</Box>
          )}
        </Box>
      )}

      <Box sx={{ flex: 1, minHeight: 0, ...(flush ? null : { px: pad, pb: pad, pt: hasHeader ? 0 : pad }) }}>
        {children}
      </Box>

      {footer && (
        <Box sx={{ px: pad, py: 1.5, borderTop: `1px solid ${tk.surfaces.border}`, bgcolor: tk.surfaces.sunken }}>
          {footer}
        </Box>
      )}
    </Surface>
  );
}
