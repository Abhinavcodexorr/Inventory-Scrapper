import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Link as RouterLink, useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Box,
  Button,
  CircularProgress,
  IconButton,
  LinearProgress,
  Skeleton,
  Tab,
  Tabs,
  Tooltip,
  Typography,
} from '@mui/material';
import { alpha, useTheme } from '@mui/material/styles';
import { LineChart } from '@mui/x-charts/LineChart';
import { BarChart } from '@mui/x-charts/BarChart';
import ArrowBackRoundedIcon from '@mui/icons-material/ArrowBackRounded';
import EditRoundedIcon from '@mui/icons-material/EditRounded';
import StopCircleRoundedIcon from '@mui/icons-material/StopCircleRounded';
import SyncRoundedIcon from '@mui/icons-material/SyncRounded';
import OpenInNewRoundedIcon from '@mui/icons-material/OpenInNew';
import DirectionsCarFilledRoundedIcon from '@mui/icons-material/DirectionsCarFilledRounded';
import SpeedRoundedIcon from '@mui/icons-material/SpeedRounded';
import TimerRoundedIcon from '@mui/icons-material/TimerRounded';
import ErrorOutlineRoundedIcon from '@mui/icons-material/ErrorOutlineRounded';
import { useSnackbar } from 'notistack';
import {
  cancelSync,
  getCurrentSyncStatus,
  getDealer,
  listDealerRuns,
  syncDealer,
} from '../api/dealers';
import { getDealerTrend } from '../api/dashboard';
import { listVehicles } from '../api/vehicles';
import { getErrorMessage } from '../api/client';
import {
  AppearIn,
  ChartFrame,
  DataTable,
  EmptyState,
  KeyValueGrid,
  PageHeader,
  ProgressRing,
  SectionCard,
  SoftIconTile,
  StatCard,
  StatusChip,
  Surface,
  useChartSx,
  type Column,
  type KeyValueItem,
} from '../components/ui';
import { useNow } from '../hooks/useNow';
import { useTokens } from '../theme/useTokens';
import { useAppSettings } from '../theme/settings';
import { chartSeries } from '../theme/tokens';
import {
  colorForName,
  formatAxisDate,
  formatDate,
  formatDuration,
  formatMoney,
  formatRelative,
  formatSeconds,
  initials,
  percent,
  vehicleTitle,
} from '../utils/format';
import type { SyncRun, Vehicle } from '../types';
import { isRunSuccess } from '../utils/runStatus';
import { summarizeRun } from '../utils/runSummary';

const SYNC_POLL_MS = 2000;
// How long to keep waiting for a dispatched sync to show up as a run before
// assuming nothing will pick it up (queued with no Celery worker running).
const SYNC_GIVE_UP_MS = 120_000;

const TABS = ['overview', 'inventory', 'history', 'analytics'] as const;
type Tab = (typeof TABS)[number];

export function DealerDetailPage() {
  const { id } = useParams();
  const dealerId = Number(id);
  const navigate = useNavigate();
  const theme = useTheme();
  const tk = useTokens();
  const chartSx = useChartSx();
  const qc = useQueryClient();
  const { settings } = useAppSettings();
  const { enqueueSnackbar } = useSnackbar();

  const [tab, setTab] = useState<Tab>('overview');
  const [invPage, setInvPage] = useState(0);
  const [invPageSize, setInvPageSize] = useState(settings.pageSize);

  const enabled = Number.isFinite(dealerId);

  const dealerQ = useQuery({ queryKey: ['dealer', dealerId], queryFn: () => getDealer(dealerId), enabled });

  const runsQ = useQuery({
    queryKey: ['dealer', dealerId, 'runs'],
    queryFn: () => listDealerRuns(dealerId, 50),
    enabled: enabled && (tab === 'history' || tab === 'analytics' || tab === 'overview'),
  });

  const trendQ = useQuery({
    queryKey: ['dashboard', 'trend', dealerId, 30],
    queryFn: () => getDealerTrend(dealerId, 30),
    enabled: enabled && (tab === 'analytics' || tab === 'overview'),
  });

  // Sold vehicles are kept (a dealer's site lists them with a Sold badge), so the
  // card can reconcile "for sale" with the number the website shows.
  const soldQ = useQuery({
    queryKey: ['vehicles', { dealer_id: dealerId, status: 'sold', count: true }],
    queryFn: () => listVehicles({ dealer_id: dealerId, status: 'sold', page: 1, page_size: 1 }),
    enabled: enabled && tab === 'overview',
  });

  const vehiclesQ = useQuery({
    queryKey: ['vehicles', { dealer_id: dealerId, page: invPage + 1, pageSize: invPageSize }],
    queryFn: () => listVehicles({ dealer_id: dealerId, page: invPage + 1, page_size: invPageSize }),
    enabled: enabled && tab === 'inventory',
  });

  // A sync we just dispatched but the server has not confirmed as started yet.
  // Between POST /sync and the worker writing its ScrapeRun row, a poll would
  // otherwise report the PREVIOUS run and we'd think the sync was already done.
  const [awaitingRunAfter, setAwaitingRunAfter] = useState<number | null>(null);
  const dispatchedAt = useRef(0);

  const syncQ = useQuery({
    queryKey: ['dealer', dealerId, 'sync-status'],
    queryFn: () => getCurrentSyncStatus(dealerId),
    enabled,
    refetchInterval: (query) =>
      query.state.data?.job_status === 'STARTED' || awaitingRunAfter !== null ? SYNC_POLL_MS : false,
  });

  const isSyncing = syncQ.data?.job_status === 'STARTED' || awaitingRunAfter !== null;
  const progress = syncQ.data?.progress ?? null;
  const now = useNow(1000, isSyncing);

  const [stopping, setStopping] = useState(false);
  useEffect(() => {
    if (!isSyncing) setStopping(false);
  }, [isSyncing]);

  const cancelM = useMutation({
    mutationFn: () => cancelSync(dealerId, syncQ.data?.job_id || undefined),
    onSuccess: (res) => {
      if (res.status === 'not_running') {
        setStopping(false);
        enqueueSnackbar(res.detail, { variant: 'info' });
        return;
      }
      setStopping(true);
      enqueueSnackbar(res.detail, { variant: 'info' });
      syncQ.refetch();
    },
    onError: (e) => {
      setStopping(false);
      enqueueSnackbar(getErrorMessage(e), { variant: 'error' });
    },
  });

  const syncM = useMutation({
    mutationFn: () => syncDealer(dealerId),
    onSuccess: (job) => {
      enqueueSnackbar(
        job.status === 'running'
          ? 'A sync is already running for this dealership'
          : `Sync started (${job.mode === 'local' ? 'in-process' : 'queued'})`,
        { variant: job.status === 'running' ? 'info' : 'success' },
      );
      setAwaitingRunAfter(syncQ.data?.latest_run?.id ?? 0);
      dispatchedAt.current = Date.now();
      syncQ.refetch();
    },
    onError: (e) => enqueueSnackbar(getErrorMessage(e), { variant: 'error' }),
  });

  const wasSyncing = useRef(false);
  useEffect(() => {
    const data = syncQ.data;
    if (awaitingRunAfter !== null && data) {
      const run = data.latest_run;
      const isNewer = run != null && run.id > awaitingRunAfter;
      const settled = isNewer && run.status !== 'running';
      const stalled = Date.now() - dispatchedAt.current > SYNC_GIVE_UP_MS;
      if (settled || (stalled && data.job_status !== 'STARTED')) {
        setAwaitingRunAfter(null);
        if (stalled && !settled) {
          enqueueSnackbar('Sync was queued but no worker picked it up — is the Celery worker running?', {
            variant: 'warning',
          });
        }
      }
    }

    if (wasSyncing.current && !isSyncing) {
      const status = syncQ.data?.latest_run?.status;
      const message =
        status === 'failed'
          ? 'Sync failed — see Scrape History'
          : status === 'cancelled'
            ? 'Sync stopped — vehicles scraped before the stop were kept'
            : `Sync ${status ?? 'finished'}`;
      enqueueSnackbar(message, {
        variant: status === 'failed' ? 'error' : status === 'cancelled' ? 'warning' : 'success',
      });
      qc.invalidateQueries({ queryKey: ['dealer', dealerId] });
      qc.invalidateQueries({ queryKey: ['dealers'] });
      qc.invalidateQueries({ queryKey: ['vehicles'] });
      qc.invalidateQueries({ queryKey: ['dashboard'] });
    }
    wasSyncing.current = isSyncing;
  }, [isSyncing, syncQ.data, awaitingRunAfter, dealerId, qc, enqueueSnackbar]);

  useEffect(() => {
    if (dealerQ.isError) enqueueSnackbar(getErrorMessage(dealerQ.error), { variant: 'error' });
  }, [dealerQ.isError, dealerQ.error, enqueueSnackbar]);

  const runStats = useMemo(() => {
    const runs = runsQ.data ?? [];
    const finished = runs.filter((r) => r.status !== 'running');
    const succeeded = finished.filter((r) => isRunSuccess(r.status)).length;
    const durations = finished
      .filter((r) => r.finished_at)
      .map((r) => (new Date(r.finished_at!).getTime() - new Date(r.started_at).getTime()) / 1000);
    return {
      successRate: finished.length ? percent(succeeded, finished.length) : 0,
      avgDuration: durations.length ? durations.reduce((a, b) => a + b, 0) / durations.length : 0,
      total: finished.length,
    };
  }, [runsQ.data]);

  const runColumns: Column<SyncRun>[] = useMemo(
    () => [
      {
        key: 'started_at',
        header: 'Started',
        minWidth: 150,
        value: (r) => r.started_at,
        render: (r) => (
          <Tooltip title={formatDate(r.started_at)}>
            <Typography variant="body2">{formatRelative(r.started_at)}</Typography>
          </Tooltip>
        ),
      },
      {
        key: 'duration',
        header: 'Duration',
        width: 100,
        sortable: false,
        value: (r) =>
          r.finished_at
            ? new Date(r.finished_at).getTime() - new Date(r.started_at).getTime()
            : r.status === 'running'
              ? now - new Date(r.started_at).getTime()
              : null,
        render: (r) => (
          <Typography variant="body2" color="text.secondary">
            {formatDuration(r.started_at, r.finished_at, r.status === 'running' ? now : undefined)}
          </Typography>
        ),
      },
      {
        key: 'status',
        header: 'Status',
        width: 120,
        value: (r) => r.status,
        render: (r) => <StatusChip status={r.status} />,
      },
      { key: 'listed', header: 'Listed', width: 90, align: 'right', value: (r) => summarizeRun(r).listed },
      { key: 'ignored', header: 'Ignored', width: 90, align: 'right', value: (r) => summarizeRun(r).ignored },
      { key: 'new_count', header: 'New', width: 80, align: 'right', value: (r) => r.new_count },
      { key: 'updated_count', header: 'Updated', width: 90, align: 'right', value: (r) => r.updated_count },
      { key: 'price_changed_count', header: 'Repriced', width: 90, align: 'right', value: (r) => r.price_changed_count },
      { key: 'removed_count', header: 'Removed', width: 90, align: 'right', value: (r) => r.removed_count },
      { key: 'sold_count', header: 'Sold', width: 80, align: 'right', value: (r) => r.sold_count },
      {
        key: 'error',
        header: 'Error',
        minWidth: 180,
        sortable: false,
        value: (r) => r.error,
        render: (r) =>
          r.error ? (
            <Tooltip title={r.error}>
              <Typography variant="body2" color="error.main" noWrap sx={{ maxWidth: 220 }}>
                {r.error}
              </Typography>
            </Tooltip>
          ) : (
            <Typography variant="body2" color="text.disabled">
              —
            </Typography>
          ),
      },
    ],
    [now],
  );

  const vehicleColumns: Column<Vehicle>[] = useMemo(
    () => [
      {
        key: 'vehicle',
        header: 'Vehicle',
        minWidth: 220,
        sortable: false,
        hideable: false,
        render: (v) => (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, minWidth: 0 }}>
            <VehicleThumb />
            <Typography variant="body2" noWrap sx={{ fontWeight: 540 }}>
              {vehicleTitle(v)}
            </Typography>
          </Box>
        ),
      },
      { key: 'trim', header: 'Trim', minWidth: 120, sortable: false, value: (v) => v.trim },
      {
        key: 'price',
        header: 'Price',
        width: 110,
        align: 'right',
        sortable: false,
        value: (v) => (v.price ? Number(v.price) : null),
        render: (v) => (
          <Typography variant="body2" sx={{ fontWeight: 600 }}>
            {formatMoney(v.price, v.currency)}
          </Typography>
        ),
      },
      {
        key: 'status',
        header: 'Status',
        width: 110,
        sortable: false,
        value: (v) => v.status,
        render: (v) => <StatusChip status={v.status} />,
      },
      {
        key: 'last_seen_at',
        header: 'Last seen',
        width: 140,
        sortable: false,
        value: (v) => v.last_seen_at,
        render: (v) => (
          <Typography variant="body2" color="text.secondary">
            {formatRelative(v.last_seen_at)}
          </Typography>
        ),
      },
    ],
    [],
  );

  if (dealerQ.isLoading) return <DealerDetailSkeleton />;

  if (dealerQ.isError || !dealerQ.data) {
    return (
      <Box>
        <PageHeader
          title="Dealership unavailable"
          leading={
            <IconButton onClick={() => navigate('/dealers')} aria-label="Back to dealerships">
              <ArrowBackRoundedIcon />
            </IconButton>
          }
        />
        <SectionCard>
          <EmptyState
            art="error"
            title="We couldn't load this dealership"
            description={getErrorMessage(dealerQ.error)}
            action={
              <Button variant="contained" onClick={() => dealerQ.refetch()}>
                Try again
              </Button>
            }
            secondaryAction={
              <Button variant="outlined" component={RouterLink} to="/dealers">
                Back to dealerships
              </Button>
            }
          />
        </SectionCard>
      </Box>
    );
  }

  const d = dealerQ.data;
  const accent = colorForName(d.name, chartSeries);

  const overviewDetails: KeyValueItem[] = [
    { label: 'Provider', value: d.website_provider || 'Unknown', muted: !d.website_provider },
    { label: 'Adapter mode', value: d.adapter_mode },
    { label: 'City / province', value: [d.city, d.province].filter(Boolean).join(', ') || 'Not set', muted: !d.city },
    { label: 'Postal code', value: d.postal_code || 'Not set', muted: !d.postal_code },
    { label: 'Phone', value: d.phone || 'Not set', muted: !d.phone },
    { label: 'Address', value: d.address || 'Not set', wide: true, muted: !d.address },
  ];

  return (
    <Box>
      <PageHeader
        leading={
          <Tooltip title="Back to dealerships">
            <IconButton onClick={() => navigate('/dealers')} aria-label="Back to dealerships" sx={{ mt: 0.5 }}>
              <ArrowBackRoundedIcon />
            </IconButton>
          </Tooltip>
        }
        eyebrow={d.website_provider ?? 'Dealership'}
        title={
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
            <Box
              sx={{
                width: 36,
                height: 36,
                borderRadius: `${tk.radii.sm + 2}px`,
                display: 'grid',
                placeItems: 'center',
                fontSize: '0.9rem',
                fontWeight: 700,
                color: accent,
                bgcolor: alpha(accent, theme.palette.mode === 'light' ? 0.12 : 0.2),
                flexShrink: 0,
              }}
            >
              {initials(d.name)}
            </Box>
            {d.name}
          </Box>
        }
        description={
          <Box
            component="a"
            href={d.base_url}
            target="_blank"
            rel="noopener noreferrer"
            sx={{ color: 'text.secondary', textDecoration: 'none', '&:hover': { textDecoration: 'underline' } }}
          >
            {d.base_url}
          </Box>
        }
        meta={<StatusChip status={d.active ? 'active' : 'inactive'} size="medium" />}
        actions={
          <>
            <Button
              variant="outlined"
              startIcon={<OpenInNewRoundedIcon />}
              href={d.base_url}
              target="_blank"
              rel="noopener noreferrer"
            >
              Visit site
            </Button>
            <Button variant="outlined" startIcon={<EditRoundedIcon />} component={RouterLink} to={`/dealers/${d.id}/edit`}>
              Edit
            </Button>
            {isSyncing && (
              <Button
                variant="outlined"
                color="warning"
                startIcon={<StopCircleRoundedIcon />}
                disabled={stopping || cancelM.isPending}
                onClick={() => cancelM.mutate()}
              >
                {stopping ? 'Stopping…' : 'Stop'}
              </Button>
            )}
            <Button
              variant="contained"
              startIcon={
                isSyncing || syncM.isPending ? <CircularProgress size={16} color="inherit" /> : <SyncRoundedIcon />
              }
              disabled={!d.active || isSyncing || syncM.isPending}
              onClick={() => syncM.mutate()}
            >
              {isSyncing ? 'Syncing…' : 'Sync now'}
            </Button>
          </>
        }
      />

      {!d.active && (
        <Surface sx={{ p: 2, mb: 2, bgcolor: alpha(theme.palette.warning.main, 0.06), borderColor: alpha(theme.palette.warning.main, 0.24) }}>
          <Typography variant="body2" sx={{ fontWeight: 560 }}>
            This dealership is inactive — activate it from the dealerships list to enable syncing.
          </Typography>
        </Surface>
      )}

      {isSyncing && (
        <Surface sx={{ p: 2.5, mb: 3 }}>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1.25 }}>
            <Typography variant="body2" sx={{ fontWeight: 560 }}>
              Scraping… {(progress?.scraped_count ?? 0).toLocaleString()}
              {progress?.reported_total ? ` / ${progress.reported_total.toLocaleString()}` : ''} vehicles
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {progress ? `${formatSeconds(progress.elapsed_seconds)} elapsed` : ''}
              {progress?.new_count ? ` · ${progress.new_count} new` : ''}
            </Typography>
          </Box>
          <LinearProgress
            variant={progress?.percent != null ? 'determinate' : 'indeterminate'}
            value={progress?.percent ?? 0}
          />
        </Surface>
      )}

      {/* ── Quick stats ────────────────────────────────────────────────── */}
      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: { xs: 'repeat(2, 1fr)', md: 'repeat(4, 1fr)' },
          gap: 2,
          mb: 3,
        }}
      >
        <AppearIn index={0}>
          <StatCard
            label="Inventory"
            value={d.inventory_count}
            icon={<DirectionsCarFilledRoundedIcon />}
            color={chartSeries[0]}
            deltaCaption={
              soldQ.data?.meta.total
                ? `for sale · ${soldQ.data.meta.total.toLocaleString()} sold · ${(d.inventory_count + soldQ.data.meta.total).toLocaleString()} listed`
                : 'active vehicles'
            }
            onClick={() => setTab('inventory')}
          />
        </AppearIn>
        <AppearIn index={1}>
          <InfoTile
            label="Last sync"
            value={formatRelative(d.last_synced_at)}
            icon={<TimerRoundedIcon />}
            color={chartSeries[1]}
          />
        </AppearIn>
        <AppearIn index={2}>
          <InfoTile
            label="Sync duration"
            value={formatSeconds(d.last_sync_duration_seconds)}
            icon={<SpeedRoundedIcon />}
            color={chartSeries[2]}
          />
        </AppearIn>
        <AppearIn index={3}>
          <InfoTile
            label="Last status"
            value={d.last_sync_status ? d.last_sync_status.replace(/_/g, ' ') : 'Never synced'}
            icon={<ErrorOutlineRoundedIcon />}
            color={d.last_sync_status === 'failed' ? theme.palette.error.main : chartSeries[3]}
            capitalize
          />
        </AppearIn>
      </Box>

      <Tabs value={tab} onChange={(_, v: Tab) => setTab(v)} sx={{ mb: 2, borderBottom: `1px solid ${tk.surfaces.border}` }}>
        <Tab label="Overview" value="overview" />
        <Tab label="Inventory" value="inventory" />
        <Tab label="Scrape History" value="history" />
        <Tab label="Analytics" value="analytics" />
      </Tabs>

      {tab === 'overview' && (
        <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', lg: '1.4fr 1fr' }, gap: 2 }}>
          <AppearIn>
            <SectionCard title="Dealership details" sx={{ height: '100%' }}>
              <Box sx={{ pt: 1 }}>
                <KeyValueGrid items={overviewDetails} columns={2} />
              </Box>
            </SectionCard>
          </AppearIn>
          <AppearIn index={1}>
            <SectionCard title="Latest scraping activity" sx={{ height: '100%' }}>
              {runsQ.isLoading ? (
                <Box sx={{ pt: 2 }}>
                  {[0, 1, 2].map((i) => (
                    <Skeleton key={i} height={48} sx={{ mb: 1 }} />
                  ))}
                </Box>
              ) : (runsQ.data ?? []).length === 0 ? (
                <EmptyState
                  dense
                  art="jobs"
                  title="No runs yet"
                  description="Sync this dealership to start recording scrape history."
                />
              ) : (
                <Box sx={{ display: 'grid', gap: 0.5, pt: 1 }}>
                  {(runsQ.data ?? []).slice(0, 6).map((run) => (
                    <Box
                      key={run.id}
                      sx={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 1.5,
                        p: 1.25,
                        borderRadius: `${tk.radii.sm + 2}px`,
                        '&:hover': { bgcolor: tk.surfaces.hover },
                      }}
                    >
                      <Box sx={{ minWidth: 0, flex: 1 }}>
                        <Typography variant="body2" sx={{ fontWeight: 540 }}>
                          {formatRelative(run.started_at)}
                        </Typography>
                        <Typography variant="caption" color="text.secondary">
                          {summarizeRun(run).text}
                        </Typography>
                      </Box>
                      <StatusChip status={run.status} />
                    </Box>
                  ))}
                </Box>
              )}
            </SectionCard>
          </AppearIn>
        </Box>
      )}

      {tab === 'inventory' && (
        <SectionCard flush>
          <DataTable
            rows={vehiclesQ.data?.items ?? []}
            columns={vehicleColumns}
            getRowId={(r) => r.id}
            loading={vehiclesQ.isLoading}
            onRowClick={(v) => navigate(`/vehicles/${v.id}`)}
            page={invPage}
            pageSize={invPageSize}
            rowCount={vehiclesQ.data?.meta.total ?? 0}
            onPageChange={setInvPage}
            onPageSizeChange={(size) => {
              setInvPageSize(size);
              setInvPage(0);
            }}
            pageSizeOptions={[25, 50, 100]}
            rowNoun="vehicles"
            empty={
              <EmptyState
                art="vehicles"
                title="No inventory yet"
                description="Sync this dealership to pull in its current listings."
                action={
                  <Button variant="contained" onClick={() => syncM.mutate()} disabled={!d.active || syncM.isPending}>
                    Sync now
                  </Button>
                }
              />
            }
          />
        </SectionCard>
      )}

      {tab === 'history' && (
        <SectionCard flush>
          <DataTable
            rows={runsQ.data ?? []}
            columns={runColumns}
            getRowId={(r) => r.id}
            loading={runsQ.isLoading}
            page={0}
            pageSize={25}
            pageSizeOptions={[25, 50]}
            rowNoun="runs"
            empty={
              <EmptyState
                art="jobs"
                title="No scrape history"
                description="Every sync run — successful or failed — will be logged here."
              />
            }
          />
        </SectionCard>
      )}

      {tab === 'analytics' && (
        <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', lg: '2fr 1fr' }, gap: 2 }}>
          <AppearIn>
            <SectionCard title="Inventory history" description="Active vehicles over the last 30 days" sx={{ height: '100%' }}>
              <ChartFrame
                loading={trendQ.isLoading}
                isEmpty={(trendQ.data?.length ?? 0) < 2}
                height={280}
                emptyTitle="Not enough history"
                emptyDescription="This chart fills in as daily snapshots accumulate."
              >
                <LineChart
                  height={280}
                  margin={{ left: 8, right: 12, top: 16, bottom: 24 }}
                  grid={{ horizontal: true }}
                  hideLegend
                  sx={chartSx}
                  series={[
                    {
                      data: (trendQ.data ?? []).map((s) => s.total_active),
                      label: 'Active vehicles',
                      area: true,
                      color: theme.palette.primary.main,
                      showMark: false,
                    },
                  ]}
                  xAxis={[
                    {
                      scaleType: 'point',
                      data: (trendQ.data ?? []).map((s) => s.snapshot_date),
                      valueFormatter: (v: string) => formatAxisDate(v),
                    },
                  ]}
                />
              </ChartFrame>
            </SectionCard>
          </AppearIn>

          <AppearIn index={1}>
            <SectionCard title="Success rate" description={`Across ${runStats.total} completed runs`} sx={{ height: '100%' }}>
              <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: 280, gap: 2 }}>
                {runStats.total === 0 ? (
                  <EmptyState dense art="jobs" title="No completed runs" />
                ) : (
                  <>
                    <ProgressRing
                      value={runStats.successRate}
                      size={128}
                      label="succeeded"
                      color={
                        runStats.successRate >= 90
                          ? theme.palette.success.main
                          : runStats.successRate >= 70
                            ? theme.palette.warning.main
                            : theme.palette.error.main
                      }
                    />
                    <Typography variant="body2" color="text.secondary">
                      Avg duration {formatSeconds(runStats.avgDuration)}
                    </Typography>
                  </>
                )}
              </Box>
            </SectionCard>
          </AppearIn>

          <AppearIn index={2} sx={{ height: '100%', gridColumn: { lg: 'span 2' } }}>
            <SectionCard
              title="Sync duration"
              description="Seconds elapsed per run"
              sx={{ height: '100%' }}
            >
              <ChartFrame
                loading={runsQ.isLoading}
                isEmpty={(runsQ.data ?? []).length === 0}
                height={240}
                emptyTitle="No runs yet"
              >
                <BarChart
                  height={240}
                  margin={{ left: 8, right: 12, top: 16, bottom: 24 }}
                  grid={{ horizontal: true }}
                  hideLegend
                  sx={chartSx}
                  series={[
                    {
                      data: [...(runsQ.data ?? [])]
                        .reverse()
                        .slice(-24)
                        .map((r) =>
                          r.finished_at
                            ? Math.round((new Date(r.finished_at).getTime() - new Date(r.started_at).getTime()) / 1000)
                            : null,
                        ),
                      label: 'Duration (s)',
                      color: theme.palette.primary.main,
                    },
                  ]}
                  xAxis={[
                    {
                      scaleType: 'band',
                      data: [...(runsQ.data ?? [])].reverse().slice(-24).map((r) => formatAxisDate(r.started_at)),
                    },
                  ]}
                />
              </ChartFrame>
            </SectionCard>
          </AppearIn>
        </Box>
      )}
    </Box>
  );
}

interface InfoTileProps {
  label: string;
  value: string;
  icon: ReactNode;
  color: string;
  capitalize?: boolean;
}

/** Text-valued companion to StatCard — for facts (timestamps, durations, status) rather than counted metrics. */
function InfoTile({ label, value, icon, color, capitalize }: InfoTileProps) {
  return (
    <Surface sx={{ p: 2.5, minHeight: 168, display: 'flex', flexDirection: 'column' }}>
      <Box sx={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 1.5 }}>
        <Typography variant="overline" sx={{ color: 'text.secondary', pt: 0.5, letterSpacing: '0.05em' }}>
          {label}
        </Typography>
        <SoftIconTile color={color}>{icon}</SoftIconTile>
      </Box>
      <Typography
        sx={{
          mt: 'auto',
          fontSize: 'clamp(1.25rem, 1.8vw, 1.5rem)',
          fontWeight: 660,
          letterSpacing: '-0.02em',
          lineHeight: 1.25,
          textTransform: capitalize ? 'capitalize' : 'none',
        }}
        noWrap
        title={value}
      >
        {value}
      </Typography>
    </Surface>
  );
}

function VehicleThumb() {
  const tk = useTokens();

  return (
    <Box
      sx={{
        width: 44,
        height: 32,
        flexShrink: 0,
        borderRadius: `${tk.radii.sm}px`,
        bgcolor: tk.surfaces.sunken,
        display: 'grid',
        placeItems: 'center',
        color: 'text.disabled',
      }}
    >
      <DirectionsCarFilledRoundedIcon sx={{ fontSize: 15 }} />
    </Box>
  );
}

function DealerDetailSkeleton() {
  return (
    <Box>
      <Skeleton width={280} height={40} />
      <Skeleton width={220} height={22} sx={{ mt: 1, mb: 3 }} />
      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: 'repeat(2, 1fr)', md: 'repeat(4, 1fr)' }, gap: 2, mb: 3 }}>
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} variant="rectangular" height={168} sx={{ borderRadius: 4 }} />
        ))}
      </Box>
      <Skeleton variant="rectangular" height={400} sx={{ borderRadius: 4 }} />
    </Box>
  );
}
