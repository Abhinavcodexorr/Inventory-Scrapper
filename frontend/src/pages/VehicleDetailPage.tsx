import { useEffect, useMemo } from 'react';
import { Link as RouterLink, useNavigate, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Box, Button, IconButton, Skeleton, Tooltip, Typography } from '@mui/material';
import { useTheme } from '@mui/material/styles';
import { LineChart } from '@mui/x-charts/LineChart';
import OpenInNewRoundedIcon from '@mui/icons-material/OpenInNew';
import ArrowBackRoundedIcon from '@mui/icons-material/ArrowBackRounded';
import DirectionsCarFilledRoundedIcon from '@mui/icons-material/DirectionsCarFilledRounded';
import StorefrontRoundedIcon from '@mui/icons-material/StorefrontRounded';
import ContentCopyRoundedIcon from '@mui/icons-material/ContentCopyRounded';
import HistoryRoundedIcon from '@mui/icons-material/HistoryRounded';
import TrendingDownRoundedIcon from '@mui/icons-material/TrendingDownRounded';
import TrendingUpRoundedIcon from '@mui/icons-material/TrendingUpRounded';
import CheckCircleRoundedIcon from '@mui/icons-material/CheckCircleRounded';
import { useSnackbar } from 'notistack';
import { getAuditLog, getPriceHistory, getVehicle, listVehicles } from '../api/vehicles';
import { getDealer } from '../api/dealers';
import { getErrorMessage } from '../api/client';
import {
  ActivityTimeline,
  AppearIn,
  ChartFrame,
  EmptyState,
  KeyValueGrid,
  PageHeader,
  SectionCard,
  StatusChip,
  TrendPill,
  useChartSx,
  type KeyValueItem,
  type TimelineItem,
} from '../components/ui';
import { useTokens } from '../theme/useTokens';
import {
  fieldOrPending,
  formatDate,
  formatMoney,
  formatMoneyCompact,
  formatRelative,
  vehicleTitle,
} from '../utils/format';

export function VehicleDetailPage() {
  const { id } = useParams();
  const vehicleId = Number(id);
  const navigate = useNavigate();
  const theme = useTheme();
  const tk = useTokens();
  const chartSx = useChartSx();
  const { enqueueSnackbar } = useSnackbar();

  const enabled = Number.isFinite(vehicleId);

  const vehicleQ = useQuery({ queryKey: ['vehicle', vehicleId], queryFn: () => getVehicle(vehicleId), enabled });
  const priceQ = useQuery({
    queryKey: ['vehicle', vehicleId, 'price-history'],
    queryFn: () => getPriceHistory(vehicleId),
    enabled,
  });
  const auditQ = useQuery({
    queryKey: ['vehicle', vehicleId, 'audit'],
    queryFn: () => getAuditLog(vehicleId),
    enabled,
  });
  const dealerQ = useQuery({
    queryKey: ['dealer', vehicleQ.data?.dealer_id],
    queryFn: () => getDealer(vehicleQ.data!.dealer_id),
    enabled: Boolean(vehicleQ.data?.dealer_id),
  });

  // Same dealership, same make — the most useful "what else is like this".
  const relatedQ = useQuery({
    queryKey: ['vehicles', 'related', vehicleQ.data?.dealer_id, vehicleQ.data?.make],
    queryFn: () =>
      listVehicles({
        dealer_id: vehicleQ.data!.dealer_id,
        make: vehicleQ.data!.make ?? undefined,
        status: 'active',
        page_size: 8,
      }),
    enabled: Boolean(vehicleQ.data?.dealer_id),
  });

  useEffect(() => {
    if (vehicleQ.isError) enqueueSnackbar(getErrorMessage(vehicleQ.error), { variant: 'error' });
  }, [vehicleQ.isError, vehicleQ.error, enqueueSnackbar]);

  const priceStats = useMemo(() => {
    const history = priceQ.data ?? [];
    if (history.length < 2) return null;
    const first = Number(history[0].price);
    const last = Number(history[history.length - 1].price);
    if (!Number.isFinite(first) || !Number.isFinite(last)) return null;
    return { change: last - first, pct: first === 0 ? 0 : ((last - first) / first) * 100, drops: history.length - 1 };
  }, [priceQ.data]);

  const timeline = useMemo<TimelineItem[]>(() => {
    return (auditQ.data ?? []).map((event) => {
      const type = event.event_type.toLowerCase();
      const removed = type.includes('remov');
      const sold = type.includes('sold');
      const price = type.includes('price');
      return {
        id: event.id,
        color: removed
          ? theme.palette.error.main
          : sold
            ? theme.palette.info.main
            : price
              ? theme.palette.warning.main
              : theme.palette.success.main,
        icon: price ? (
          <TrendingDownRoundedIcon />
        ) : removed ? (
          <HistoryRoundedIcon />
        ) : sold ? (
          <CheckCircleRoundedIcon />
        ) : (
          <TrendingUpRoundedIcon />
        ),
        title: event.event_type.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase()),
        description:
          [event.old_status, event.new_status].filter(Boolean).join(' → ') || event.note || undefined,
        timestamp: formatRelative(event.occurred_at),
      };
    });
  }, [auditQ.data, theme.palette]);

  if (vehicleQ.isLoading) return <VehicleDetailSkeleton />;

  if (vehicleQ.isError || !vehicleQ.data) {
    return (
      <Box>
        <PageHeader
          title="Vehicle unavailable"
          leading={
            <IconButton onClick={() => navigate('/vehicles')} aria-label="Back to inventory">
              <ArrowBackRoundedIcon />
            </IconButton>
          }
        />
        <SectionCard>
          <EmptyState
            art="error"
            title="We couldn't load this vehicle"
            description={getErrorMessage(vehicleQ.error)}
            action={
              <Button variant="contained" onClick={() => vehicleQ.refetch()}>
                Try again
              </Button>
            }
            secondaryAction={
              <Button variant="outlined" component={RouterLink} to="/vehicles">
                Back to inventory
              </Button>
            }
          />
        </SectionCard>
      </Box>
    );
  }

  const v = vehicleQ.data;

  const specs: KeyValueItem[] = [
    { label: 'Stock number', value: v.external_id },
    { label: 'VIN', value: v.vin ?? 'Not captured', muted: !v.vin },
    { label: 'Condition', value: fieldOrPending(v.condition), muted: !v.condition },
    { label: 'Mileage', value: v.mileage != null ? `${v.mileage.toLocaleString()} km` : 'Not captured', muted: v.mileage == null },
    { label: 'Body type', value: fieldOrPending(v.body_type), muted: !v.body_type },
    { label: 'Fuel', value: fieldOrPending(v.fuel_type), muted: !v.fuel_type },
    { label: 'Transmission', value: fieldOrPending(v.transmission), muted: !v.transmission },
    { label: 'Drivetrain', value: fieldOrPending(v.drivetrain), muted: !v.drivetrain },
    { label: 'Exterior colour', value: fieldOrPending(v.exterior_color), muted: !v.exterior_color },
    { label: 'Interior colour', value: fieldOrPending(v.interior_color), muted: !v.interior_color },
    { label: 'Currency', value: v.currency },
    { label: 'Trim', value: fieldOrPending(v.trim), muted: !v.trim },
    {
      label: 'Was priced at',
      value: v.original_price ? formatMoney(v.original_price, v.currency) : 'Not on sale',
      muted: !v.original_price,
    },
  ];

  const scrapeMeta: KeyValueItem[] = [
    { label: 'Created', value: formatDate(v.first_seen_at) },
    // Two different questions: is the data fresh, and did the listing change.
    { label: 'Last checked', value: formatDate(v.last_seen_at) },
    {
      label: 'Last updated',
      value: v.updated_at ? formatDate(v.updated_at) : 'No changes yet',
      muted: !v.updated_at,
    },
    { label: 'Removed at', value: formatDate(v.removed_at), muted: !v.removed_at },
    { label: 'Sold at', value: formatDate(v.sold_at), muted: !v.sold_at },
    {
      label: 'Source listing',
      wide: true,
      value: v.url ? (
        <Box
          component="a"
          href={v.url}
          target="_blank"
          rel="noopener noreferrer"
          sx={{ color: 'primary.main', textDecoration: 'none', wordBreak: 'break-all', '&:hover': { textDecoration: 'underline' } }}
        >
          {v.url}
        </Box>
      ) : (
        'Not captured'
      ),
      muted: !v.url,
    },
  ];

  const related = (relatedQ.data?.items ?? []).filter((item) => item.id !== v.id).slice(0, 6);

  return (
    <Box>
      <PageHeader
        eyebrow={dealerQ.data ? dealerQ.data.name : 'Vehicle'}
        title={vehicleTitle(v)}
        leading={
          <Tooltip title="Back to inventory">
            <IconButton onClick={() => navigate(-1)} aria-label="Go back" sx={{ mt: 0.5 }}>
              <ArrowBackRoundedIcon />
            </IconButton>
          </Tooltip>
        }
        meta={
          <>
            <StatusChip status={v.status} size="medium" />
            <Typography variant="h4" sx={{ fontWeight: 700 }}>
              {formatMoney(v.price, v.currency)}
            </Typography>
            {priceStats && priceStats.change !== 0 && (
              <TrendPill value={Math.round(priceStats.pct)} suffix="%" invert caption="since first seen" />
            )}
            {dealerQ.data && (
              <Button
                size="small"
                component={RouterLink}
                to={`/dealers/${v.dealer_id}`}
                startIcon={<StorefrontRoundedIcon sx={{ fontSize: 16 }} />}
              >
                {dealerQ.data.name}
              </Button>
            )}
          </>
        }
        actions={
          <>
            {v.vin && (
              <Button
                variant="outlined"
                startIcon={<ContentCopyRoundedIcon />}
                onClick={() => {
                  navigator.clipboard
                    ?.writeText(v.vin!)
                    .then(() => enqueueSnackbar('VIN copied', { variant: 'success' }))
                    .catch(() => enqueueSnackbar('Could not copy to clipboard', { variant: 'error' }));
                }}
              >
                Copy VIN
              </Button>
            )}
            {v.url && (
              <Button
                variant="contained"
                endIcon={<OpenInNewRoundedIcon />}
                href={v.url}
                target="_blank"
                rel="noopener noreferrer"
              >
                Original listing
              </Button>
            )}
          </>
        }
      />

      <Box sx={{ mb: 2 }}>
        <AppearIn>
          <SectionCard title="Price history" description="Every price the scraper has recorded" sx={{ height: '100%' }}>
            <ChartFrame
              loading={priceQ.isLoading}
              isEmpty={(priceQ.data?.length ?? 0) < 2}
              height={240}
              emptyTitle="No price changes yet"
              emptyDescription="The listing has held a single price since it was first seen."
            >
              <LineChart
                height={240}
                margin={{ left: 8, right: 12, top: 16, bottom: 24 }}
                grid={{ horizontal: true }}
                hideLegend
                sx={chartSx}
                series={[
                  {
                    data: (priceQ.data ?? []).map((p) => Number(p.price)),
                    label: 'Price',
                    area: true,
                    curve: 'stepAfter',
                    color: theme.palette.primary.main,
                    valueFormatter: (value, { dataIndex }) => {
                      const point = priceQ.data?.[dataIndex];
                      if (point) return formatMoney(point.price, point.currency);
                      return value == null ? '—' : formatMoney(String(value), v.currency);
                    },
                  },
                ]}
                xAxis={[
                  {
                    scaleType: 'point',
                    data: (priceQ.data ?? []).map((p) => p.recorded_at),
                    valueFormatter: (value: string) => formatRelative(value),
                  },
                ]}
                yAxis={[{ width: 56, valueFormatter: (value: number | null) => formatMoneyCompact(value) }]}
              />
            </ChartFrame>

            {priceStats && (
              <Box sx={{ display: 'flex', gap: 3, mt: 1, pt: 2, borderTop: `1px solid ${tk.surfaces.border}` }}>
                <Box>
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                    Net change
                  </Typography>
                  <Typography
                    variant="subtitle1"
                    sx={{ fontWeight: 660, color: priceStats.change <= 0 ? 'success.main' : 'error.main' }}
                  >
                    {priceStats.change > 0 ? '+' : ''}
                    {formatMoney(String(Math.round(priceStats.change)), v.currency)}
                  </Typography>
                </Box>
                <Box>
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                    Repricings
                  </Typography>
                  <Typography variant="subtitle1" sx={{ fontWeight: 660 }}>
                    {priceStats.drops}
                  </Typography>
                </Box>
              </Box>
            )}
          </SectionCard>
        </AppearIn>
      </Box>

      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', lg: '1.6fr 1fr' }, gap: 2, mb: 2, '& > *': { minWidth: 0 } }}>
        <AppearIn index={1}>
          <SectionCard title="Specifications" sx={{ height: '100%' }}>
            <Box sx={{ pt: 1 }}>
              <KeyValueGrid items={specs} columns={3} />
            </Box>
          </SectionCard>
        </AppearIn>

        <AppearIn index={2}>
          <SectionCard title="Lifecycle" description="Status changes recorded by the scraper" sx={{ height: '100%' }}>
            {auditQ.isLoading ? (
              <Box sx={{ pt: 2 }}>
                {[0, 1, 2].map((i) => (
                  <Skeleton key={i} height={44} sx={{ mb: 1 }} />
                ))}
              </Box>
            ) : timeline.length === 0 ? (
              <EmptyState
                dense
                art="chart"
                title="No lifecycle events"
                description="Events appear when this vehicle changes price or status."
              />
            ) : (
              <Box sx={{ pt: 1 }}>
                <ActivityTimeline items={timeline} maxHeight={340} />
              </Box>
            )}
          </SectionCard>
        </AppearIn>
      </Box>

      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', lg: '1fr 1fr' }, gap: 2, '& > *': { minWidth: 0 } }}>
        <AppearIn index={1}>
          <SectionCard
            title="Scraping metadata"
            description="Where this record came from and when it was last confirmed"
            sx={{ height: '100%' }}
          >
            <Box sx={{ pt: 1 }}>
              <KeyValueGrid items={scrapeMeta} columns={2} />
            </Box>
          </SectionCard>
        </AppearIn>

        <AppearIn index={2}>
          <SectionCard
            title="Related vehicles"
            description={v.make ? `More ${v.make} at this dealership` : 'More from this dealership'}
            sx={{ height: '100%' }}
          >
            {relatedQ.isLoading ? (
              <Box sx={{ pt: 1 }}>
                {[0, 1, 2].map((i) => (
                  <Skeleton key={i} height={56} sx={{ mb: 1 }} />
                ))}
              </Box>
            ) : related.length === 0 ? (
              <EmptyState
                dense
                art="vehicles"
                title="Nothing comparable"
                description="This dealership has no other active vehicles of the same make."
              />
            ) : (
              <Box sx={{ display: 'grid', gap: 0.5, pt: 1 }}>
                {related.map((item) => (
                  <Box
                    key={item.id}
                    onClick={() => navigate(`/vehicles/${item.id}`)}
                    sx={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 1.5,
                      p: 1,
                      borderRadius: `${tk.radii.sm + 2}px`,
                      cursor: 'pointer',
                      transition: `background-color ${tk.motion.fast} ${tk.motion.ease}`,
                      '&:hover': { bgcolor: tk.surfaces.hover },
                    }}
                  >
                    <RelatedThumb />
                    <Box sx={{ minWidth: 0, flex: 1 }}>
                      <Typography variant="body2" noWrap sx={{ fontWeight: 540 }}>
                        {vehicleTitle(item)}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        {item.mileage != null ? `${item.mileage.toLocaleString()} km` : 'Mileage not captured'}
                      </Typography>
                    </Box>
                    <Typography variant="body2" sx={{ fontWeight: 620, flexShrink: 0 }}>
                      {formatMoney(item.price, item.currency)}
                    </Typography>
                  </Box>
                ))}
              </Box>
            )}
          </SectionCard>
        </AppearIn>
      </Box>
    </Box>
  );
}

function RelatedThumb() {
  const tk = useTokens();

  return (
    <Box
      sx={{
        width: 56,
        height: 42,
        flexShrink: 0,
        borderRadius: `${tk.radii.sm}px`,
        bgcolor: tk.surfaces.sunken,
        display: 'grid',
        placeItems: 'center',
        color: 'text.disabled',
      }}
    >
      <DirectionsCarFilledRoundedIcon sx={{ fontSize: 18 }} />
    </Box>
  );
}

function VehicleDetailSkeleton() {
  return (
    <Box>
      <Skeleton width={280} height={40} />
      <Skeleton width={200} height={28} sx={{ mt: 1, mb: 3 }} />
      <Skeleton variant="rectangular" height={340} sx={{ borderRadius: 4, mb: 2 }} />
      <Skeleton variant="rectangular" height={260} sx={{ borderRadius: 4 }} />
    </Box>
  );
}
