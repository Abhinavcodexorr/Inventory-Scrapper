import { Box, Skeleton } from '@mui/material';
import { Surface } from '../components/ui';

/**
 * Skeleton shown while a lazily-loaded route resolves. Mirrors the standard page
 * shape — header, KPI row, panel — so the layout does not shift on arrival.
 */
export function PageFallback() {
  return (
    <Box>
      <Box sx={{ mb: 3 }}>
        <Skeleton width={220} height={38} />
        <Skeleton width={340} height={20} sx={{ mt: 1 }} />
      </Box>

      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: { xs: 'repeat(2, 1fr)', md: 'repeat(4, 1fr)' },
          gap: 2,
          mb: 3,
        }}
      >
        {[0, 1, 2, 3].map((i) => (
          <Surface key={i} sx={{ p: 2.5, height: 168 }}>
            <Skeleton width="60%" height={14} />
            <Skeleton width="45%" height={38} sx={{ mt: 2 }} />
            <Skeleton width="35%" height={18} sx={{ mt: 1 }} />
          </Surface>
        ))}
      </Box>

      <Surface sx={{ p: 2.5, height: 360 }}>
        <Skeleton width={180} height={22} />
        <Skeleton variant="rectangular" height={280} sx={{ mt: 2, borderRadius: 2 }} />
      </Surface>
    </Box>
  );
}
