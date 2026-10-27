import { useId, useMemo } from 'react';
import { Box } from '@mui/material';

interface Props {
  data: readonly number[];
  color: string;
  height?: number;
  /** Fill the area beneath the line with a fading gradient. */
  area?: boolean;
  strokeWidth?: number;
  /** Draw a dot on the final point. */
  showEndDot?: boolean;
  /** Animate the line drawing itself in on mount. */
  animate?: boolean;
}

/**
 * Dependency-free sparkline. Rendered as a stretched viewBox so it scales to any
 * container width without a resize observer, and uses a Catmull-Rom → cubic
 * conversion so the curve is smooth without overshooting the data.
 */
export function Sparkline({
  data,
  color,
  height = 40,
  area = true,
  strokeWidth = 2,
  showEndDot = true,
  animate = true,
}: Props) {
  const gradientId = useId();
  const W = 100;
  const H = 32;
  const pad = 3;

  const { line, fill, endPoint } = useMemo(() => {
    const points = data.length ? data : [0, 0];
    const min = Math.min(...points);
    const max = Math.max(...points);
    // A flat series should sit on the centre line, not collapse to the floor.
    const span = max - min || 1;
    const stepX = points.length > 1 ? (W - pad * 2) / (points.length - 1) : 0;

    const coords = points.map((v, i) => {
      const x = pad + i * stepX;
      const y = max === min ? H / 2 : H - pad - ((v - min) / span) * (H - pad * 2);
      return [x, y] as const;
    });

    if (coords.length === 1) coords.push([W - pad, coords[0][1]]);

    let d = `M ${coords[0][0]} ${coords[0][1]}`;
    for (let i = 0; i < coords.length - 1; i += 1) {
      const p0 = coords[i - 1] ?? coords[i];
      const p1 = coords[i];
      const p2 = coords[i + 1];
      const p3 = coords[i + 2] ?? p2;
      // Catmull-Rom control points, tension 1/6 — smooth but faithful.
      const c1x = p1[0] + (p2[0] - p0[0]) / 6;
      const c1y = p1[1] + (p2[1] - p0[1]) / 6;
      const c2x = p2[0] - (p3[0] - p1[0]) / 6;
      const c2y = p2[1] - (p3[1] - p1[1]) / 6;
      d += ` C ${c1x} ${c1y}, ${c2x} ${c2y}, ${p2[0]} ${p2[1]}`;
    }

    const last = coords[coords.length - 1];
    return {
      line: d,
      fill: `${d} L ${last[0]} ${H} L ${coords[0][0]} ${H} Z`,
      endPoint: last,
    };
  }, [data]);

  return (
    <Box
      component="svg"
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      aria-hidden="true"
      focusable="false"
      sx={{
        display: 'block',
        width: '100%',
        height,
        overflow: 'visible',
        '@keyframes sparkline-draw': { from: { strokeDashoffset: 240 }, to: { strokeDashoffset: 0 } },
      }}
    >
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.28" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      {area && <path d={fill} fill={`url(#${gradientId})`} stroke="none" />}
      <Box
        component="path"
        d={line}
        fill="none"
        stroke={color}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
        sx={
          animate
            ? {
                strokeDasharray: 240,
                animation: 'sparkline-draw 900ms cubic-bezier(0.16, 1, 0.3, 1) forwards',
                '@media (prefers-reduced-motion: reduce)': { animation: 'none', strokeDasharray: 'none' },
              }
            : undefined
        }
      />
      {showEndDot && (
        <circle
          cx={endPoint[0]}
          cy={endPoint[1]}
          r={2}
          fill={color}
          stroke="var(--sparkline-dot-ring, transparent)"
          strokeWidth={1.5}
          vectorEffect="non-scaling-stroke"
        />
      )}
    </Box>
  );
}
