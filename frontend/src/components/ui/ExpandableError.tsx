import { useLayoutEffect, useRef, useState } from 'react';
import { Box, Link, Typography } from '@mui/material';

const COLLAPSED_LINES = 2;

/**
 * A run's error message for a table cell: two lines, then Read more / Read less.
 * Clicks and key presses stay inside the cell, so selecting the text to copy it
 * or using the toggle does not trigger the row's navigation.
 */
export function ExpandableError({ text }: { text: string }) {
  const textRef = useRef<HTMLElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [overflows, setOverflows] = useState(false);

  // Measured rather than guessed from the length: how much fits depends on the column width.
  useLayoutEffect(() => {
    const el = textRef.current;
    if (!el || expanded) return;
    const measure = () => setOverflows(el.scrollHeight > el.clientHeight + 1);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [text, expanded]);

  return (
    <Box
      onClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => e.stopPropagation()}
      sx={{ cursor: 'auto', minWidth: 0 }}
    >
      <Typography
        ref={textRef}
        variant="body2"
        color="error.main"
        sx={{
          overflowWrap: 'anywhere',
          whiteSpace: 'pre-wrap',
          ...(expanded
            ? null
            : {
                display: '-webkit-box',
                WebkitBoxOrient: 'vertical',
                WebkitLineClamp: COLLAPSED_LINES,
                overflow: 'hidden',
              }),
        }}
      >
        {text}
      </Typography>
      {(overflows || expanded) && (
        <Link
          component="button"
          type="button"
          variant="caption"
          aria-expanded={expanded}
          onClick={() => setExpanded((v) => !v)}
          sx={{ display: 'block', mt: 0.25, fontWeight: 560 }}
        >
          {expanded ? 'Read less' : 'Read more'}
        </Link>
      )}
    </Box>
  );
}
