import type { ReactNode } from 'react';
import { Box, Typography } from '@mui/material';

interface Props {
  title: ReactNode;
  description?: ReactNode;
  /** Small label above the title — context, count or category. */
  eyebrow?: ReactNode;
  /** Status chips / metadata rendered under the title. */
  meta?: ReactNode;
  actions?: ReactNode;
  /** Back link or avatar rendered to the left of the title block. */
  leading?: ReactNode;
}

/** Consistent page title block: eyebrow, title, description, meta and actions. */
export function PageHeader({ title, description, eyebrow, meta, actions, leading }: Props) {
  return (
    <Box
      sx={{
        display: 'flex',
        alignItems: { xs: 'stretch', md: 'flex-start' },
        justifyContent: 'space-between',
        flexDirection: { xs: 'column', md: 'row' },
        gap: 2,
        mb: 3,
      }}
    >
      <Box sx={{ display: 'flex', gap: 2, minWidth: 0, alignItems: 'flex-start' }}>
        {leading}
        <Box sx={{ minWidth: 0 }}>
          {eyebrow && (
            <Typography variant="overline" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
              {eyebrow}
            </Typography>
          )}
          <Typography variant="h3" sx={{ wordBreak: 'break-word' }}>
            {title}
          </Typography>
          {description && (
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.75, maxWidth: 680 }}>
              {description}
            </Typography>
          )}
          {meta && (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mt: 1.5, flexWrap: 'wrap' }}>{meta}</Box>
          )}
        </Box>
      </Box>

      {actions && (
        <Box
          sx={{
            display: 'flex',
            gap: 1,
            flexShrink: 0,
            flexWrap: 'wrap',
            alignItems: 'center',
            justifyContent: { xs: 'flex-start', md: 'flex-end' },
          }}
        >
          {actions}
        </Box>
      )}
    </Box>
  );
}
