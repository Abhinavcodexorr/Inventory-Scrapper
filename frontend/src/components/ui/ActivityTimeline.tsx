import type { ReactNode } from 'react';
import { Box, Typography } from '@mui/material';
import { alpha, useTheme } from '@mui/material/styles';
import { useTokens } from '../../theme/useTokens';

export interface TimelineItem {
  id: string | number;
  icon: ReactNode;
  color: string;
  title: ReactNode;
  description?: ReactNode;
  /** Pre-formatted timestamp string. */
  timestamp: string;
  /** Trailing badge/chip. */
  trailing?: ReactNode;
  onClick?: () => void;
}

interface Props {
  items: TimelineItem[];
  /** Cap the visible list; the rest stay scrollable. */
  maxHeight?: number;
}

/**
 * Vertical activity rail. Each entry is a hoverable card connected by a hairline
 * so a sequence of events reads as one thread rather than a stack of rows.
 */
export function ActivityTimeline({ items, maxHeight }: Props) {
  const tk = useTokens();
  const theme = useTheme();

  return (
    <Box
      component="ol"
      sx={{
        listStyle: 'none',
        m: 0,
        p: 0,
        maxHeight,
        overflowY: maxHeight ? 'auto' : 'visible',
        pr: maxHeight ? 0.5 : 0,
      }}
    >
      {items.map((item, i) => {
        const isLast = i === items.length - 1;
        return (
          <Box
            component="li"
            key={item.id}
            sx={{
              position: 'relative',
              display: 'flex',
              gap: 2,
              pb: isLast ? 0 : 2.5,
              // The rail: a hairline dropping from each marker to the next.
              '&::before': isLast
                ? undefined
                : {
                    content: '""',
                    position: 'absolute',
                    left: 15,
                    top: 34,
                    bottom: 6,
                    width: '1px',
                    bgcolor: tk.surfaces.border,
                  },
            }}
          >
            <Box
              aria-hidden="true"
              sx={{
                position: 'relative',
                zIndex: 1,
                width: 32,
                height: 32,
                flexShrink: 0,
                borderRadius: '50%',
                display: 'grid',
                placeItems: 'center',
                color: item.color,
                bgcolor: alpha(item.color, theme.palette.mode === 'light' ? 0.1 : 0.18),
                boxShadow: `0 0 0 4px ${theme.palette.background.paper}, inset 0 0 0 1px ${alpha(item.color, 0.2)}`,
                '& > svg': { fontSize: 16 },
              }}
            >
              {item.icon}
            </Box>

            <Box
              onClick={item.onClick}
              sx={{
                flex: 1,
                minWidth: 0,
                mt: -0.25,
                borderRadius: `${tk.radii.sm + 2}px`,
                px: 1.25,
                py: 0.75,
                ml: -1.25,
                cursor: item.onClick ? 'pointer' : 'default',
                transition: `background-color ${tk.motion.fast} ${tk.motion.ease}`,
                '&:hover': item.onClick ? { bgcolor: tk.surfaces.hover } : undefined,
              }}
            >
              <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 1, justifyContent: 'space-between' }}>
                <Typography variant="body2" sx={{ fontWeight: 560, minWidth: 0 }}>
                  {item.title}
                </Typography>
                <Typography variant="caption" color="text.disabled" sx={{ flexShrink: 0, whiteSpace: 'nowrap' }}>
                  {item.timestamp}
                </Typography>
              </Box>
              {item.description && (
                <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.25 }}>
                  {item.description}
                </Typography>
              )}
              {item.trailing && <Box sx={{ mt: 0.75 }}>{item.trailing}</Box>}
            </Box>
          </Box>
        );
      })}
    </Box>
  );
}
