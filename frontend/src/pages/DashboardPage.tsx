import { useId, useMemo } from 'react';
import { Link as RouterLink, useNavigate } from 'react-router-dom';
import { Alert, Avatar, Box, Button, CircularProgress, LinearProgress, Typography } from '@mui/material';
import { alpha, useTheme } from '@mui/material/styles';
import { LineChart } from '@mui/x-charts/LineChart';
import { BarChart } from '@mui/x-charts/BarChart';
import { PieChart } from '@mui/x-charts/PieChart';
import StorefrontRoundedIcon from '@mui/icons-material/StorefrontRounded';
import CheckCircleRoundedIcon from '@mui/icons-material/CheckCircleRounded';
import DirectionsCarFilledRoundedIcon from '@mui/icons-material/DirectionsCarFilledRounded';
import FiberNewRoundedIcon from '@mui/icons-material/FiberNewRounded';
import ErrorOutlineRoundedIcon from '@mui/icons-material/ErrorOutlineRounded';
import BoltRoundedIcon from '@mui/icons-material/BoltRounded';
import SpeedRoundedIcon from '@mui/icons-material/SpeedRounded';
import AddRoundedIcon from '@mui/icons-material/AddRounded';
import ArrowForwardRoundedIcon from '@mui/icons-material/ArrowForwardRounded';
import SyncRoundedIcon from '@mui/icons-material/SyncRounded';
import RemoveCircleOutlineRoundedIcon from '@mui/icons-material/RemoveCircleOutlineRounded';
import PriceChangeRoundedIcon from '@mui/icons-material/PriceChangeRounded';
import {
  ActivityTimeline,
  AppearIn,
  ChartFrame,
  EmptyState,
  ProgressRing,
  SectionCard,
  StatCard,
  StatusChip,
  Surface,
  TrendPill,
  useChartSx,
  type TimelineItem,
} from '../components/ui';
import {
  aggregateByDate,
  useAllRuns,
  useAllTrends,
  useDashboardSummary,
  useDealerList,
  useDealerStats,
} from '../hooks/useDealers';
import { useRunScraperAction } from '../layout/runScraperContext';
import { useTokens } from '../theme/useTokens';
import { useAppSettings } from '../theme/settings';
import { chartSeries } from '../theme/tokens';
import { isRunSuccess } from '../utils/runStatus';
import { summarizeRun } from '../utils/runSummary';
import { FeedReconciliationCard } from '../components/FeedReconciliationCard';
import {
  colorForName,
  formatAxisDate,
  formatCompact,
  formatRelative,
  greeting,
  initials,
  percent,
} from '../utils/format';

const TREND_DAYS = 14;
/** Runs pulled per dealer for the activity feed and success-rate maths. */
const RUNS_PER_DEALER = 5;

/** Subtle fractal-noise tile layered over the hero gradient — adds grain
 *  without an image asset. Same opacity works in both themes since the hero
 *  gradient itself doesn't change with mode. */
const HERO_NOISE_BG =
  "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='180' height='180'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/%3E%3CfeColorMatrix type='saturate' values='0'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E\")";

export function DashboardPage() {
  const theme = useTheme();
  const tk = useTokens();
  const navigate = useNavigate();
  const chartSx = useChartSx();
  const areaGradientId = useId();
  const { settings } = useAppSettings();
  const runScraper = useRunScraperAction();

  const dealersQ = useDealerList();
  const summaryQ = useDashboardSummary();
  const statsQ = useDealerStats();

  // Both of these fan out one request per dealer — there is no aggregate
  // endpoint. They share cache keys with the Jobs and Analytics pages, so
  // navigating between them is instant rather than refetching.
  const trends = useAllTrends(dealersQ.data, TREND_DAYS, Boolean(dealersQ.data?.length));
  const runsAgg = useAllRuns(dealersQ.data, RUNS_PER_DEALER, Boolean(dealersQ.data?.length));

  const daily = useMemo(() => aggregateByDate(trends.snapshots), [trends.snapshots]);
  const summary = summaryQ.data;

  /** Latest run per dealer → the "is the platform healthy right now" numbers. */
  const runHealth = useMemo(() => {
    const latestByDealer = new Map<number, (typeof runsAgg.runs)[number]>();
    for (const run of runsAgg.runs) {
      if (!latestByDealer.has(run.dealer_id)) latestByDealer.set(run.dealer_id, run);
    }
    const latest = [...latestByDealer.values()];
    const succeeded = latest.filter((r) => isRunSuccess(r.status)).length;
    const running = runsAgg.runs.filter((r) => r.status === 'running').length;

    const startOfToday = new Date();
    startOfToday.setHours(0, 0, 0, 0);
    const todayRuns = runsAgg.runs.filter((r) => new Date(r.started_at) >= startOfToday);
    const priceChangesToday = todayRuns.reduce((sum, r) => sum + r.price_changed_count, 0);

    const durations = latest
      .filter((r) => r.finished_at)
      .map((r) => (new Date(r.finished_at!).getTime() - new Date(r.started_at).getTime()) / 1000)
      .filter((s) => s >= 0);

    return {
      successRate: latest.length ? percent(succeeded, latest.length) : 0,
      running,
      priceChangesToday,
      avgDurationSeconds: durations.length ? durations.reduce((a, b) => a + b, 0) / durations.length : 0,
      sampled: latest.length,
    };
  }, [runsAgg.runs]);

  const today = daily.at(-1);
  const yesterday = daily.at(-2);

  const loadingCore = summaryQ.isLoading || dealersQ.isLoading;
  const hasNeverSynced =
    Boolean(summary) && !summary!.last_sync_at && summary!.vehicles_active === 0 && summary!.added_today === 0;

  /** Recent scrape activity as a readable event stream. */
  const activity = useMemo<TimelineItem[]>(() => {
    return runsAgg.runs.slice(0, 12).map((run) => {
      const failed = run.status === 'failed';
      const running = run.status === 'running';
      return {
        id: run.id,
        color: failed
          ? theme.palette.error.main
          : running
            ? theme.palette.primary.main
            : run.new_count > 0
              ? theme.palette.success.main
              : theme.palette.text.secondary,
        icon: failed ? (
          <ErrorOutlineRoundedIcon />
        ) : running ? (
          <SyncRoundedIcon />
        ) : run.new_count > 0 ? (
          <FiberNewRoundedIcon />
        ) : run.removed_count > 0 ? (
          <RemoveCircleOutlineRoundedIcon />
        ) : (
          <CheckCircleRoundedIcon />
        ),
        title: run.dealer_name,
        description: failed
          ? (run.error ?? 'Scrape failed')
          : running
            ? `Scraping — ${run.scraped_count.toLocaleString()} vehicles so far`
            : summarizeRun(run).changes.length
              ? summarizeRun(run).text
              : `${summarizeRun(run).headline}, no changes`,
        timestamp: formatRelative(run.started_at),
        onClick: () => navigate(`/dealers/${run.dealer_id}`),
      };
    });
  }, [runsAgg.runs, theme.palette, navigate]);

  /** Inventory share per dealership, largest first, with a long tail bucket. */
  const distribution = useMemo(() => {
    const sorted = [...(dealersQ.data ?? [])]
      .filter((d) => d.inventory_count > 0)
      .sort((a, b) => b.inventory_count - a.inventory_count);
    const top: { id: number; value: number; label: string; color: string }[] = sorted.slice(0, 6).map((d, i) => ({
      id: d.id,
      value: d.inventory_count,
      label: d.name,
      color: chartSeries[i % chartSeries.length],
    }));
    const rest = sorted.slice(6).reduce((sum, d) => sum + d.inventory_count, 0);
    if (rest > 0) {
      top.push({ id: -1, value: rest, label: `${sorted.length - 6} more`, color: theme.palette.text.disabled });
    }
    return top;
  }, [dealersQ.data, theme.palette.text.disabled]);

  /** Top dealerships by today's volume, ranked for the leaderboard. */
  const leaderboard = useMemo(() => {
    const sorted = [...(statsQ.data ?? [])].sort((a, b) => b.total_today - a.total_today).slice(0, 8);
    const topTotal = sorted[0]?.total_today || 1;
    return { sorted, topTotal };
  }, [statsQ.data]);

  return (
    <Box>
      {/* ── Hero ───────────────────────────────────────────────────────── */}
      <AppearIn>
        <Surface
          sx={{
            position: 'relative',
            overflow: 'hidden',
            p: { xs: 3, md: 4 },
            mb: 3,
            border: 'none',
            color: '#fff',
            background: `linear-gradient(120deg, ${theme.palette.primary.dark} 0%, ${theme.palette.primary.main} 46%, ${alpha(theme.palette.secondary.main, 0.92)} 100%)`,
            boxShadow: tk.elevation.md,
            '&::after': {
              content: '""',
              position: 'absolute',
              inset: 0,
              background:
                'radial-gradient(70% 120% at 88% -10%, rgba(255,255,255,0.22) 0%, transparent 60%), radial-gradient(50% 90% at 8% 110%, rgba(255,255,255,0.12) 0%, transparent 60%)',
              pointerEvents: 'none',
            },
          }}
        >
          <Box
            aria-hidden
            sx={{
              position: 'absolute',
              inset: 0,
              zIndex: 1,
              backgroundImage: HERO_NOISE_BG,
              backgroundSize: '180px 180px',
              mixBlendMode: 'overlay',
              opacity: 0.06,
              pointerEvents: 'none',
            }}
          />
          <Box
            sx={{
              position: 'relative',
              zIndex: 2,
              display: 'flex',
              gap: 3,
              flexDirection: { xs: 'column', lg: 'row' },
              alignItems: { lg: 'flex-end' },
              justifyContent: 'space-between',
            }}
          >
            <Box sx={{ minWidth: 0 }}>
              <Typography variant="h2" sx={{ color: '#fff', mb: 0.75 }}>
                {greeting()}, {settings.profileName} 👋
              </Typography>
              <Typography variant="body1" sx={{ color: alpha('#fff', 0.86), maxWidth: 560 }}>
                {hasNeverSynced
                  ? 'No scrape has run yet — add a dealership and start your first sync to populate the platform.'
                  : (summary?.dealers_failed_last_run ?? 0) > 0
                    ? `${summary!.dealers_failed_last_run} dealership${summary!.dealers_failed_last_run === 1 ? '' : 's'} failed on the last run and need attention.`
                    : 'Your inventory platform is running smoothly.'}
              </Typography>

              <Box sx={{ display: 'flex', gap: { xs: 3, sm: 5 }, mt: 3, flexWrap: 'wrap' }}>
                <HeroStat
                  value={formatCompact(summary?.vehicles_active ?? 0)}
                  label="Vehicles tracked"
                  loading={loadingCore}
                />
                <HeroStat
                  value={String(summary?.dealers_active ?? 0)}
                  label="Active dealerships"
                  loading={loadingCore}
                />
                <HeroStat
                  value={runHealth.sampled ? `${runHealth.successRate.toFixed(0)}%` : '—'}
                  label="Successful sync rate"
                  loading={runsAgg.isLoading}
                />
              </Box>
            </Box>

            <Box sx={{ display: 'flex', gap: 1.5, flexShrink: 0, flexWrap: 'wrap' }}>
              <Button
                size="large"
                onClick={runScraper.open}
                disabled={runScraper.isRunning}
                startIcon={
                  runScraper.isRunning ? (
                    <CircularProgress size={16} sx={{ color: 'inherit' }} />
                  ) : (
                    <BoltRoundedIcon />
                  )
                }
                sx={{
                  bgcolor: '#fff',
                  color: theme.palette.primary.dark,
                  '&:hover': { bgcolor: alpha('#fff', 0.9) },
                  '&.Mui-disabled': { bgcolor: alpha('#fff', 0.6), color: alpha(theme.palette.primary.dark, 0.6) },
                }}
              >
                Run Scraper
              </Button>
              <Button
                size="large"
                component={RouterLink}
                to="/dealers/new"
                startIcon={<AddRoundedIcon />}
                sx={{
                  color: '#fff',
                  border: `1px solid ${alpha('#fff', 0.4)}`,
                  '&:hover': { bgcolor: alpha('#fff', 0.14), borderColor: alpha('#fff', 0.6) },
                }}
              >
                Add Dealer
              </Button>
            </Box>
          </Box>
        </Surface>
      </AppearIn>

      {(summaryQ.isError || dealersQ.isError) && (
        <Alert severity="error" sx={{ mb: 3 }}>
          Could not reach the API. Confirm the backend is running and reachable at its configured address.
        </Alert>
      )}

      {/* ── KPI grid ───────────────────────────────────────────────────── */}
      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: {
            xs: 'repeat(1, minmax(0, 1fr))',
            sm: 'repeat(2, minmax(0, 1fr))',
            lg: 'repeat(4, minmax(0, 1fr))',
          },
          gap: 1.5,
          mb: 2.5,
        }}
      >
        {[
          {
            label: 'Total dealers',
            value: summary?.dealers_total ?? 0,
            icon: <StorefrontRoundedIcon />,
            color: theme.palette.text.secondary,
            deltaCaption: `${summary?.dealers_active ?? 0} active`,
            onClick: () => navigate('/dealers'),
          },
          {
            label: 'Active dealers',
            value: summary?.dealers_active ?? 0,
            icon: <CheckCircleRoundedIcon />,
            color: theme.palette.success.main,
            deltaCaption:
              (summary?.dealers_total ?? 0) > 0
                ? `${percent(summary?.dealers_active ?? 0, summary?.dealers_total ?? 0).toFixed(0)}% of sources`
                : 'No dealerships yet',
            onClick: () => navigate('/dealers'),
          },
          {
            label: 'Vehicles',
            value: summary?.vehicles_active ?? 0,
            icon: <DirectionsCarFilledRoundedIcon />,
            color: theme.palette.primary.main,
            emphasis: true,
            delta: today && yesterday ? today.total - yesterday.total : undefined,
            deltaCaption: 'vs yesterday',
            series: daily.map((d) => d.total),
            onClick: () => navigate('/vehicles'),
          },
          {
            label: 'New today',
            value: summary?.added_today ?? 0,
            icon: <FiberNewRoundedIcon />,
            color: theme.palette.text.secondary,
            delta: today && yesterday ? today.added - yesterday.added : undefined,
            deltaCaption: 'vs yesterday',
            series: daily.map((d) => d.added),
            // Deep-link to the matching tab: a number the operator cannot
            // drill into is the gap this whole screen had.
            onClick: () => navigate('/changes?tab=added'),
          },
          {
            label: 'Price changes',
            value: runHealth.priceChangesToday,
            icon: <PriceChangeRoundedIcon />,
            color: theme.palette.text.secondary,
            deltaCaption: 'recorded today',
            series: daily.map((d) => d.priceChanged),
            onClick: () => navigate('/changes?tab=price'),
          },
          {
            label: 'Failed scrapes',
            value: summary?.dealers_failed_last_run ?? 0,
            icon: <ErrorOutlineRoundedIcon />,
            color: theme.palette.error.main,
            alert: true,
            deltaCaption: 'on the last run',
            onClick: () => navigate('/jobs'),
          },
          {
            label: 'Running jobs',
            value: runHealth.running,
            icon: <BoltRoundedIcon />,
            color: theme.palette.text.secondary,
            deltaCaption: runHealth.running > 0 ? 'scraping now' : 'idle',
            onClick: () => navigate('/jobs'),
          },
          {
            label: 'Success rate',
            value: runHealth.successRate,
            decimals: 0,
            suffix: '%',
            icon: <SpeedRoundedIcon />,
            color:
              runHealth.successRate >= 90
                ? theme.palette.success.main
                : runHealth.successRate >= 70
                  ? theme.palette.warning.main
                  : theme.palette.error.main,
            deltaCaption: runHealth.sampled ? `across ${runHealth.sampled} dealerships` : 'no runs yet',
            onClick: () => navigate('/jobs'),
          },
        ].map((card, i) => (
          <AppearIn key={card.label} index={i} sx={{ height: '100%' }}>
            <StatCard
              label={card.label}
              value={card.value}
              icon={card.icon}
              color={card.color}
              emphasis={card.emphasis}
              delta={card.delta}
              deltaCaption={card.deltaCaption}
              decimals={card.decimals}
              suffix={card.suffix}
              series={card.series && card.series.length > 1 ? card.series : undefined}
              alert={card.alert}
              loading={loadingCore}
              onClick={card.onClick}
              dense
            />
          </AppearIn>
        ))}
      </Box>

      {hasNeverSynced && (
        <Alert
          severity="info"
          sx={{ mb: 3 }}
          action={
            <Button size="small" component={RouterLink} to="/dealers/new">
              Add dealership
            </Button>
          }
        >
          No sync has run yet. Add a dealership and run a scrape to populate these views.
        </Alert>
      )}

      {/* ── Charts ─────────────────────────────────────────────────────── */}
      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: { xs: '1fr', lg: 'minmax(0, 2fr) minmax(0, 1fr)' },
          gap: 1.5,
          mb: 2.5,
          alignItems: 'stretch',
        }}
      >
        <AppearIn index={1}>
          <SectionCard
            title="Inventory growth"
            description={`Total active vehicles across every dealership, last ${TREND_DAYS} days`}
            sx={{ height: '100%' }}
            dense
            actions={
              today && yesterday ? <TrendPill value={today.total - yesterday.total} caption="today" /> : undefined
            }
          >
            <ChartFrame
              loading={trends.isLoading}
              isEmpty={daily.length < 2}
              height={260}
              emptyTitle="Not enough history yet"
              emptyDescription="Inventory snapshots are written once per day. The trend appears after a couple of sync cycles."
            >
              <LineChart
                height={260}
                margin={{ left: 12, right: 20, top: 24, bottom: 28 }}
                grid={{ horizontal: true }}
                hideLegend
                sx={{ ...chartSx, '& .MuiAreaElement-root': { fill: `url(#${areaGradientId})`, fillOpacity: 1 } }}
                series={[
                  {
                    data: daily.map((d) => d.total),
                    label: 'Active vehicles',
                    area: true,
                    curve: 'monotoneX',
                    showMark: false,
                    color: theme.palette.primary.main,
                  },
                ]}
                xAxis={[
                  {
                    scaleType: 'point',
                    data: daily.map((d) => d.date),
                    valueFormatter: (value: string) => formatAxisDate(value),
                  },
                ]}
                yAxis={[{ width: 48, valueFormatter: (v: number | null) => formatCompact(v) }]}
              >
                <defs>
                  <linearGradient id={areaGradientId} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={theme.palette.primary.main} stopOpacity={0.32} />
                    <stop offset="100%" stopColor={theme.palette.primary.main} stopOpacity={0} />
                  </linearGradient>
                </defs>
              </LineChart>
            </ChartFrame>
          </SectionCard>
        </AppearIn>

        <AppearIn index={2}>
          <SectionCard
            title="Dealer distribution"
            description="Share of tracked inventory"
            sx={{ height: '100%' }}
            dense
          >
            <ChartFrame
              loading={dealersQ.isLoading}
              isEmpty={distribution.length === 0}
              height={260}
              emptyTitle="No inventory yet"
              emptyDescription="Once a dealership syncs, its share of the fleet appears here."
              emptyArt="dealers"
            >
              <PieChart
                height={260}
                margin={{ left: 16, right: 16, top: 16, bottom: 16 }}
                hideLegend
                sx={chartSx}
                series={[
                  {
                    data: distribution,
                    innerRadius: 52,
                    outerRadius: 92,
                    paddingAngle: 2,
                    cornerRadius: 6,
                    highlightScope: { fade: 'global', highlight: 'item' },
                    valueFormatter: (item) => `${item.value.toLocaleString()} vehicles`,
                  },
                ]}
              />
            </ChartFrame>
          </SectionCard>
        </AppearIn>
      </Box>

      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: { xs: '1fr', lg: 'minmax(0, 1fr) minmax(0, 1fr) minmax(0, 1fr)' },
          gap: 1.5,
          mb: 2.5,
        }}
      >
        <AppearIn index={1} sx={{ height: '100%', gridColumn: { lg: 'span 2' } }}>
          <SectionCard
            title="Vehicles added vs removed"
            description={`Daily movement, last ${TREND_DAYS} days`}
            sx={{ height: '100%' }}
            dense
          >
            <ChartFrame
              loading={trends.isLoading}
              isEmpty={daily.length === 0}
              height={230}
              emptyTitle="No movement recorded"
              emptyDescription="Additions and removals appear once daily snapshots exist."
            >
              <BarChart
                height={230}
                margin={{ left: 12, right: 20, top: 28, bottom: 28 }}
                grid={{ horizontal: true }}
                sx={chartSx}
                series={[
                  { data: daily.map((d) => d.added), label: 'Added', color: theme.palette.success.main, stack: 'a' },
                  {
                    data: daily.map((d) => -d.removed),
                    label: 'Removed',
                    color: theme.palette.error.main,
                    stack: 'a',
                    valueFormatter: (v: number | null) => (v == null ? '—' : String(Math.abs(v))),
                  },
                ]}
                xAxis={[
                  {
                    scaleType: 'band',
                    data: daily.map((d) => d.date),
                    valueFormatter: (value: string) => formatAxisDate(value),
                  },
                ]}
                yAxis={[{ width: 42, valueFormatter: (v: number | null) => String(Math.abs(v ?? 0)) }]}
              />
            </ChartFrame>
          </SectionCard>
        </AppearIn>

        <AppearIn index={2}>
          <SectionCard title="Scraping health" description="Latest run per dealership" sx={{ height: '100%' }} dense>
            <Box
              sx={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                height: 230,
                gap: 1.5,
              }}
            >
              {runsAgg.isLoading ? (
                <CircularProgress />
              ) : runHealth.sampled === 0 ? (
                <EmptyState
                  dense
                  art="jobs"
                  title="No runs yet"
                  description="Dispatch a scrape to start collecting health data."
                />
              ) : (
                <>
                  <ProgressRing
                    value={runHealth.successRate}
                    size={104}
                    label="succeeded"
                    color={
                      runHealth.successRate >= 90
                        ? theme.palette.success.main
                        : runHealth.successRate >= 70
                          ? theme.palette.warning.main
                          : theme.palette.error.main
                    }
                  />
                  <Box sx={{ display: 'flex', gap: 2.5, textAlign: 'center' }}>
                    <Box>
                      <Typography variant="h6">{runHealth.running}</Typography>
                      <Typography variant="caption" color="text.secondary">
                        running
                      </Typography>
                    </Box>
                    <Box>
                      <Typography variant="h6">
                        {runHealth.avgDurationSeconds >= 60
                          ? `${Math.round(runHealth.avgDurationSeconds / 60)}m`
                          : `${Math.round(runHealth.avgDurationSeconds)}s`}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        avg duration
                      </Typography>
                    </Box>
                  </Box>
                </>
              )}
            </Box>
          </SectionCard>
        </AppearIn>
      </Box>

      {/* ── Activity + dealer leaderboard ──────────────────────────────── */}
      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', lg: 'minmax(0, 1fr) minmax(0, 1fr)' }, gap: 1.5 }}>
        <AppearIn index={1}>
          <SectionCard
            title="Recent activity"
            description="Latest scrape runs across every dealership"
            sx={{ height: '100%' }}
            dense
            actions={
              <Button size="small" component={RouterLink} to="/jobs" endIcon={<ArrowForwardRoundedIcon />}>
                All jobs
              </Button>
            }
          >
            {runsAgg.isLoading ? (
              <Box sx={{ display: 'grid', placeItems: 'center', minHeight: 220 }}>
                <CircularProgress />
              </Box>
            ) : activity.length === 0 ? (
              <EmptyState
                dense
                art="jobs"
                title="No activity yet"
                description="Scrape runs will stream in here as soon as the first sync starts."
                action={
                  <Button variant="contained" onClick={runScraper.open} startIcon={<BoltRoundedIcon />}>
                    Run scraper
                  </Button>
                }
              />
            ) : (
              <Box sx={{ pt: 0.5 }}>
                <ActivityTimeline items={activity} maxHeight={340} />
              </Box>
            )}
          </SectionCard>
        </AppearIn>

        <AppearIn index={2}>
          <SectionCard
            title="Dealership performance"
            description="Today's inventory against yesterday"
            sx={{ height: '100%' }}
            dense
            actions={
              <Button size="small" component={RouterLink} to="/dealers" endIcon={<ArrowForwardRoundedIcon />}>
                All dealers
              </Button>
            }
          >
            {statsQ.isLoading ? (
              <Box sx={{ display: 'grid', placeItems: 'center', minHeight: 220 }}>
                <CircularProgress />
              </Box>
            ) : (statsQ.data ?? []).length === 0 ? (
              <EmptyState
                dense
                art="dealers"
                title="No dealerships yet"
                description="Add your first dealership to begin collecting inventory automatically."
                action={
                  <Button variant="contained" component={RouterLink} to="/dealers/new" startIcon={<AddRoundedIcon />}>
                    Add dealer
                  </Button>
                }
              />
            ) : (
              <Box sx={{ display: 'grid', gap: 0.25, pt: 0.5, pr: 1, maxHeight: 340, overflowY: 'auto' }}>
                {leaderboard.sorted.map((s) => {
                  const delta = s.total_today - s.total_yesterday;
                  const dealerColor = colorForName(s.dealer_name, chartSeries);
                  return (
                    <Box
                      key={s.dealer_id}
                      onClick={() => navigate(`/dealers/${s.dealer_id}`)}
                      sx={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 1.5,
                        px: 1,
                        py: 1,
                        borderRadius: `${tk.radii.sm + 2}px`,
                        cursor: 'pointer',
                        transition: `background-color ${tk.motion.fast} ${tk.motion.ease}`,
                        '&:hover': { bgcolor: tk.surfaces.hover },
                      }}
                    >
                      <Avatar
                        sx={{
                          width: 28,
                          height: 28,
                          fontSize: '0.7rem',
                          fontWeight: 700,
                          flexShrink: 0,
                          bgcolor: alpha(dealerColor, 0.14),
                          color: dealerColor,
                        }}
                      >
                        {initials(s.dealer_name)}
                      </Avatar>
                      <Box sx={{ minWidth: 0, flex: 1 }}>
                        <Typography variant="body2" noWrap sx={{ fontWeight: 560 }}>
                          {s.dealer_name}
                        </Typography>
                        <Typography variant="caption" color="text.secondary">
                          {s.added_today > 0 && `+${s.added_today} added · `}
                          {s.sold_today > 0 && `${s.sold_today} sold · `}
                          {s.last_sync_at ? formatRelative(s.last_sync_at) : 'never synced'}
                        </Typography>
                        <LinearProgress
                          variant="determinate"
                          value={(s.total_today / leaderboard.topTotal) * 100}
                          sx={{ mt: 0.75, maxWidth: 160 }}
                        />
                      </Box>
                      <Typography variant="body2" sx={{ fontWeight: 620, flexShrink: 0 }}>
                        {s.total_today.toLocaleString()}
                      </Typography>
                      <Box sx={{ width: 74, display: 'flex', justifyContent: 'flex-end', flexShrink: 0 }}>
                        <TrendPill value={delta} />
                      </Box>
                      <Box sx={{ width: 92, flexShrink: 0, display: { xs: 'none', sm: 'block' } }}>
                        <StatusChip status={s.sync_status} />
                      </Box>
                    </Box>
                  );
                })}
              </Box>
            )}
          </SectionCard>
        </AppearIn>
      </Box>

      <AppearIn index={3}>
        <FeedReconciliationCard stats={statsQ.data ?? []} />
      </AppearIn>
    </Box>
  );
}

/** Inline metric inside the gradient hero. */
function HeroStat({ value, label, loading }: { value: string; label: string; loading: boolean }) {
  return (
    <Box>
      <Typography sx={{ fontSize: '1.75rem', fontWeight: 700, letterSpacing: '-0.03em', lineHeight: 1.1, color: '#fff' }}>
        {loading ? '—' : value}
      </Typography>
      <Typography variant="caption" sx={{ color: alpha('#fff', 0.78), fontWeight: 500 }}>
        {label}
      </Typography>
    </Box>
  );
}
