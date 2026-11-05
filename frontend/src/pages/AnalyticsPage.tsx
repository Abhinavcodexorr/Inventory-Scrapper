import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Box, Button, Typography } from '@mui/material';
import { alpha, useTheme } from '@mui/material/styles';
import { LineChart } from '@mui/x-charts/LineChart';
import { BarChart } from '@mui/x-charts/BarChart';
import TrendingUpRoundedIcon from '@mui/icons-material/TrendingUpRounded';
import SpeedRoundedIcon from '@mui/icons-material/SpeedRounded';
import TimerRoundedIcon from '@mui/icons-material/TimerRounded';
import HourglassEmptyRoundedIcon from '@mui/icons-material/HourglassEmptyRounded';
import {
  AppearIn,
  ChartFrame,
  FilterBar,
  PageHeader,
  SectionCard,
  StatCard,
  Surface,
  useChartSx,
} from '../components/ui';
import { useQuery } from '@tanstack/react-query';
import { useFleetTrend, useInventoryShare, useRecentRuns } from '../hooks/useDealers';
import { DealerFilter } from '../components/DealerFilter';
import { getDealer } from '../api/dealers';
import { useTokens } from '../theme/useTokens';
import { chartSeries } from '../theme/tokens';
import { colorForName, formatAxisDate, formatCompact, percent } from '../utils/format';
import { isRunSuccess } from '../utils/runStatus';

const TREND_DAYS = 30;
const RECENT_RUNS = 300;

export function AnalyticsPage() {
  const navigate = useNavigate();
  const theme = useTheme();
  const tk = useTokens();
  const chartSx = useChartSx();

  const [dealerId, setDealerId] = useState('');

  const selectedId = dealerId ? Number(dealerId) : undefined;
  const shareQ = useInventoryShare(10);
  const selectedQ = useQuery({
    queryKey: ['dealer', selectedId],
    queryFn: () => getDealer(selectedId!),
    enabled: selectedId !== undefined,
  });
  const trends = useFleetTrend(TREND_DAYS, selectedId);
  const daily = trends.daily;
  const runsAgg = useRecentRuns({ limit: RECENT_RUNS, dealerId: selectedId });
  const filteredRuns = runsAgg.runs;

  /** The dealers charted: the selected one, or the ten largest. */
  const inventoryByBrandData = useMemo(() => {
    if (selectedId !== undefined) {
      const d = selectedQ.data;
      return d ? [{ id: d.id, name: d.name, inventory_count: d.inventory_count }] : [];
    }
    return shareQ.data?.top ?? [];
  }, [selectedId, selectedQ.data, shareQ.data]);
  const inventoryLoading = selectedId !== undefined ? selectedQ.isLoading : shareQ.isLoading;
  const dealerCount = selectedId !== undefined ? 1 : (shareQ.data?.dealer_count ?? 0);

  const scraperEfficiency = useMemo(() => {
    const byDealer = new Map<number, { name: string; durations: number[]; success: number; total: number }>();
    for (const r of filteredRuns) {
      if (r.status === 'running') continue;
      const entry = byDealer.get(r.dealer_id) ?? { name: r.dealer_name, durations: [], success: 0, total: 0 };
      entry.total += 1;
      if (isRunSuccess(r.status)) entry.success += 1;
      if (r.finished_at) {
        entry.durations.push((new Date(r.finished_at).getTime() - new Date(r.started_at).getTime()) / 1000);
      }
      byDealer.set(r.dealer_id, entry);
    }
    return [...byDealer.entries()]
      .map(([id, v]) => ({
        id,
        name: v.name,
        avgDuration: v.durations.length ? v.durations.reduce((a, b) => a + b, 0) / v.durations.length : 0,
        successRate: v.total ? percent(v.success, v.total) : 0,
        total: v.total,
      }))
      .sort((a, b) => b.total - a.total)
      .slice(0, 8);
  }, [filteredRuns]);

  const overallSuccessRate = useMemo(() => {
    const finished = filteredRuns.filter((r) => r.status !== 'running');
    const succeeded = finished.filter((r) => isRunSuccess(r.status)).length;
    return finished.length ? percent(succeeded, finished.length) : 0;
  }, [filteredRuns]);

  const avgDuration = useMemo(() => {
    const durations = filteredRuns
      .filter((r) => r.finished_at)
      .map((r) => (new Date(r.finished_at!).getTime() - new Date(r.started_at).getTime()) / 1000);
    return durations.length ? durations.reduce((a, b) => a + b, 0) / durations.length : 0;
  }, [filteredRuns]);

  const totalInventory = selectedId !== undefined ? (selectedQ.data?.inventory_count ?? 0) : (shareQ.data?.total ?? 0);
  const growth = daily.length >= 2 ? daily[daily.length - 1].total - daily[0].total : 0;

  return (
    <Box>
      <PageHeader
        title="Analytics"
        description="Cross-dealership trends: inventory growth, scraper efficiency and price movement over time."
      />

      <FilterBar activeCount={dealerId ? 1 : 0} onReset={() => setDealerId('')}>
        <DealerFilter value={dealerId} onChange={setDealerId} allLabel="All dealerships" width={240} />
      </FilterBar>

      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: 'repeat(2, 1fr)', md: 'repeat(4, 1fr)' }, gap: 2, mb: 3 }}>
        <AppearIn index={0}>
          <StatCard
            label={`${TREND_DAYS}-day growth`}
            value={growth}
            icon={<TrendingUpRoundedIcon />}
            color={growth >= 0 ? theme.palette.success.main : theme.palette.error.main}
            deltaCaption="net vehicles"
            series={daily.map((d) => d.total)}
            loading={trends.isLoading}
          />
        </AppearIn>
        <AppearIn index={1}>
          <StatCard
            label="Tracked inventory"
            value={totalInventory}
            icon={<HourglassEmptyRoundedIcon />}
            color={chartSeries[1]}
            deltaCaption={`across ${dealerCount.toLocaleString()} dealership${dealerCount === 1 ? '' : 's'}`}
            loading={inventoryLoading}
          />
        </AppearIn>
        <AppearIn index={2}>
          <StatCard
            label="Scraper success rate"
            value={overallSuccessRate}
            suffix="%"
            icon={<SpeedRoundedIcon />}
            color={theme.palette.success.main}
            deltaCaption="last runs"
            loading={runsAgg.isLoading}
          />
        </AppearIn>
        <AppearIn index={3}>
          <StatCard
            label="Avg sync duration"
            value={avgDuration >= 60 ? Math.round(avgDuration / 60) : Math.round(avgDuration)}
            suffix={avgDuration >= 60 ? 'm' : 's'}
            icon={<TimerRoundedIcon />}
            color={chartSeries[3]}
            loading={runsAgg.isLoading}
          />
        </AppearIn>
      </Box>

      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', lg: '2fr 1fr' }, gap: 2, mb: 2 }}>
        <AppearIn>
          <SectionCard title="Inventory trend" description={`Active vehicles, last ${TREND_DAYS} days`} sx={{ height: '100%' }}>
            <ChartFrame
              loading={trends.isLoading}
              isEmpty={daily.length < 2}
              height={300}
              emptyTitle="Not enough history yet"
            >
              <LineChart
                height={300}
                margin={{ left: 8, right: 12, top: 16, bottom: 24 }}
                grid={{ horizontal: true }}
                hideLegend
                sx={chartSx}
                series={[
                  {
                    data: daily.map((d) => d.total),
                    label: 'Active vehicles',
                    area: true,
                    color: theme.palette.primary.main,
                    showMark: false,
                  },
                ]}
                xAxis={[
                  {
                    scaleType: 'point',
                    data: daily.map((d) => d.date),
                    valueFormatter: (v: string) => formatAxisDate(v),
                  },
                ]}
                yAxis={[{ width: 48, valueFormatter: (v: number | null) => formatCompact(v) }]}
              />
            </ChartFrame>
          </SectionCard>
        </AppearIn>

        <AppearIn index={1}>
          <SectionCard title="Inventory by dealer" description="Top 10 by active vehicle count" sx={{ height: '100%' }}>
            <ChartFrame
              loading={inventoryLoading}
              isEmpty={inventoryByBrandData.length === 0}
              height={300}
              emptyTitle="No inventory yet"
              emptyArt="dealers"
            >
              <BarChart
                height={300}
                layout="horizontal"
                margin={{ left: 100, right: 16, top: 8, bottom: 8 }}
                grid={{ vertical: true }}
                hideLegend
                sx={chartSx}
                series={[
                  {
                    data: inventoryByBrandData.map((d) => d.inventory_count),
                    color: theme.palette.primary.main,
                  },
                ]}
                yAxis={[
                  {
                    scaleType: 'band',
                    data: inventoryByBrandData.map((d) => (d.name.length > 14 ? `${d.name.slice(0, 13)}…` : d.name)),
                  },
                ]}
              />
            </ChartFrame>
          </SectionCard>
        </AppearIn>
      </Box>

      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', lg: '1fr 1fr' }, gap: 2, mb: 2 }}>
        <AppearIn>
          <SectionCard title="Price changes" description={`Repricing activity, last ${TREND_DAYS} days`} sx={{ height: '100%' }}>
            <ChartFrame loading={trends.isLoading} isEmpty={daily.length === 0} height={260} emptyTitle="No price activity yet">
              <BarChart
                height={260}
                margin={{ left: 8, right: 12, top: 16, bottom: 24 }}
                grid={{ horizontal: true }}
                hideLegend
                sx={chartSx}
                series={[{ data: daily.map((d) => d.priceChanged), label: 'Repricings', color: chartSeries[3] }]}
                xAxis={[
                  {
                    scaleType: 'band',
                    data: daily.map((d) => d.date),
                    valueFormatter: (v: string) => formatAxisDate(v),
                  },
                ]}
              />
            </ChartFrame>
          </SectionCard>
        </AppearIn>

        <AppearIn index={1}>
          <SectionCard title="Sync duration trend" description="Seconds per run, most recent first" sx={{ height: '100%' }}>
            <ChartFrame
              loading={runsAgg.isLoading}
              isEmpty={filteredRuns.length === 0}
              height={260}
              emptyTitle="No runs yet"
              emptyArt="jobs"
            >
              <LineChart
                height={260}
                margin={{ left: 8, right: 12, top: 16, bottom: 24 }}
                grid={{ horizontal: true }}
                hideLegend
                sx={chartSx}
                series={[
                  {
                    data: [...filteredRuns]
                      .filter((r) => r.finished_at)
                      .reverse()
                      .slice(-30)
                      .map((r) => Math.round((new Date(r.finished_at!).getTime() - new Date(r.started_at).getTime()) / 1000)),
                    label: 'Duration (s)',
                    color: theme.palette.secondary.main,
                    showMark: false,
                    area: true,
                  },
                ]}
              />
            </ChartFrame>
          </SectionCard>
        </AppearIn>
      </Box>

      <AppearIn>
        <SectionCard title="Dealer comparison" description="Scraper efficiency by dealership">
          {scraperEfficiency.length === 0 ? (
            <Typography variant="body2" color="text.secondary" sx={{ py: 4, textAlign: 'center' }}>
              No completed runs yet.
            </Typography>
          ) : (
            <Box sx={{ display: 'grid', gap: 0.5, pt: 1 }}>
              {scraperEfficiency.map((row) => {
                const color = colorForName(row.name, chartSeries);
                return (
                  <Box
                    key={row.id}
                    onClick={() => navigate(`/dealers/${row.id}`)}
                    sx={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 2,
                      p: 1.25,
                      borderRadius: `${tk.radii.sm + 2}px`,
                      cursor: 'pointer',
                      transition: `background-color ${tk.motion.fast} ${tk.motion.ease}`,
                      '&:hover': { bgcolor: tk.surfaces.hover },
                    }}
                  >
                    <Typography variant="body2" sx={{ fontWeight: 560, minWidth: 160, flexShrink: 0 }} noWrap>
                      {row.name}
                    </Typography>
                    <Box
                      sx={{
                        flex: 1,
                        height: 8,
                        borderRadius: 999,
                        bgcolor: alpha(color, theme.palette.mode === 'light' ? 0.12 : 0.2),
                        overflow: 'hidden',
                      }}
                    >
                      <Box
                        sx={{
                          width: `${row.successRate}%`,
                          height: '100%',
                          bgcolor: color,
                          borderRadius: 999,
                          transition: 'width 700ms cubic-bezier(0.16, 1, 0.3, 1)',
                        }}
                      />
                    </Box>
                    <Typography variant="body2" sx={{ width: 50, textAlign: 'right', fontWeight: 600, flexShrink: 0 }}>
                      {row.successRate.toFixed(0)}%
                    </Typography>
                    <Typography variant="caption" color="text.secondary" sx={{ width: 90, textAlign: 'right', flexShrink: 0 }}>
                      {row.avgDuration >= 60 ? `${Math.round(row.avgDuration / 60)}m avg` : `${Math.round(row.avgDuration)}s avg`}
                    </Typography>
                  </Box>
                );
              })}
            </Box>
          )}
        </SectionCard>
      </AppearIn>

      {shareQ.data?.dealer_count === 0 && (
        <Surface sx={{ p: 3, mt: 2, textAlign: 'center' }}>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
            Add a dealership to start building analytics.
          </Typography>
          <Button variant="contained" onClick={() => navigate('/dealers/new')}>
            Add dealership
          </Button>
        </Surface>
      )}
    </Box>
  );
}
