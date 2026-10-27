import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Box, Button, IconButton, LinearProgress, Tooltip, Typography } from '@mui/material';
import { useTheme } from '@mui/material/styles';
import BoltRoundedIcon from '@mui/icons-material/BoltRounded';
import ReplayRoundedIcon from '@mui/icons-material/ReplayRounded';
import StopCircleRoundedIcon from '@mui/icons-material/StopCircleRounded';
import CheckCircleRoundedIcon from '@mui/icons-material/CheckCircleRounded';
import ErrorOutlineRoundedIcon from '@mui/icons-material/ErrorOutlineRounded';
import TimerRoundedIcon from '@mui/icons-material/TimerRounded';
import { useSnackbar } from 'notistack';
import { cancelSync, syncDealer } from '../api/dealers';
import { getErrorMessage } from '../api/client';
import {
  AppearIn,
  DataTable,
  EmptyState,
  FilterBar,
  FilterSelect,
  PageHeader,
  SearchField,
  SectionCard,
  StatCard,
  StatusChip,
  Surface,
  type Column,
} from '../components/ui';
import { useAllRuns, useDealerList } from '../hooks/useDealers';
import { useNow } from '../hooks/useNow';
import { useRunScraperAction } from '../layout/runScraperContext';
import { chartSeries } from '../theme/tokens';
import { formatDate, formatDuration, formatRelative, percent } from '../utils/format';
import { isRunSuccess } from '../utils/runStatus';
import { summarizeRun } from '../utils/runSummary';

const RUNS_PER_DEALER = 30;
const LIVE_POLL_MS = 3000;

export function JobsPage() {
  const navigate = useNavigate();
  const theme = useTheme();
  const qc = useQueryClient();
  const { enqueueSnackbar } = useSnackbar();
  const runScraper = useRunScraperAction();

  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('');
  const [dealerId, setDealerId] = useState('');

  const dealersQ = useDealerList();
  const runsAgg = useAllRuns(dealersQ.data, RUNS_PER_DEALER, Boolean(dealersQ.data?.length), LIVE_POLL_MS);

  useEffect(() => {
    if (runsAgg.isError) enqueueSnackbar('Could not load scrape run history', { variant: 'error' });
  }, [runsAgg.isError, enqueueSnackbar]);

  const running = useMemo(() => runsAgg.runs.filter((r) => r.status === 'running'), [runsAgg.runs]);
  const now = useNow(1000, running.length > 0);

  const stats = useMemo(() => {
    const finished = runsAgg.runs.filter((r) => r.status !== 'running');
    const succeeded = finished.filter((r) => isRunSuccess(r.status)).length;
    const failed = finished.filter((r) => r.status === 'failed').length;
    const durations = finished
      .filter((r) => r.finished_at)
      .map((r) => (new Date(r.finished_at!).getTime() - new Date(r.started_at).getTime()) / 1000);
    return {
      running: running.length,
      completed: succeeded,
      failed,
      successRate: finished.length ? percent(succeeded, finished.length) : 0,
      avgDuration: durations.length ? durations.reduce((a, b) => a + b, 0) / durations.length : 0,
    };
  }, [runsAgg.runs, running.length]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return runsAgg.runs.filter((r) => {
      if (status && r.status !== status) return false;
      if (dealerId && String(r.dealer_id) !== dealerId) return false;
      if (q && !r.dealer_name.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [runsAgg.runs, search, status, dealerId]);

  const activeFilterCount = [search, status, dealerId].filter(Boolean).length;

  const retryM = useMutation({
    mutationFn: syncDealer,
    onSuccess: (job) => {
      enqueueSnackbar(
        job.status === 'running' ? 'A sync is already running for this dealership' : 'Retry started',
        { variant: job.status === 'running' ? 'info' : 'success' },
      );
      qc.invalidateQueries({ queryKey: ['dealer', job.dealer_id, 'runs'] });
      qc.invalidateQueries({ queryKey: ['dealers'] });
    },
    onError: (e) => enqueueSnackbar(getErrorMessage(e), { variant: 'error' }),
  });

  const cancelM = useMutation({
    mutationFn: ({ dealerId: id, jobId }: { dealerId: number; jobId?: string }) => cancelSync(id, jobId),
    onSuccess: (res) => {
      enqueueSnackbar(res.detail, { variant: 'info' });
      qc.invalidateQueries({ queryKey: ['dealer', res.dealer_id, 'runs'] });
    },
    onError: (e) => enqueueSnackbar(getErrorMessage(e), { variant: 'error' }),
  });

  const columns: Column<(typeof runsAgg.runs)[number]>[] = useMemo(
    () => [
      {
        key: 'dealer_name',
        header: 'Dealership',
        minWidth: 180,
        hideable: false,
        value: (r) => r.dealer_name,
        render: (r) => (
          <Typography variant="body2" sx={{ fontWeight: 560 }} noWrap>
            {r.dealer_name}
          </Typography>
        ),
      },
      {
        key: 'started_at',
        header: 'Started',
        width: 140,
        value: (r) => r.started_at,
        render: (r) => (
          <Tooltip title={formatDate(r.started_at)}>
            <Typography variant="body2" color="text.secondary">
              {formatRelative(r.started_at)}
            </Typography>
          </Tooltip>
        ),
      },
      {
        key: 'status',
        header: 'Status',
        width: 200,
        value: (r) => r.status,
        render: (r) =>
          r.status === 'running' ? (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, minWidth: 140 }}>
              <StatusChip status="running" />
              <Box sx={{ flex: 1 }}>
                <LinearProgress
                  variant={r.reported_total ? 'determinate' : 'indeterminate'}
                  value={r.reported_total ? Math.min(100, percent(r.scraped_count, r.reported_total)) : undefined}
                  sx={{ height: 4 }}
                />
              </Box>
            </Box>
          ) : (
            <StatusChip status={r.status} />
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
      { key: 'listed', header: 'Listed', width: 90, align: 'right', value: (r) => summarizeRun(r).listed },
      { key: 'ignored', header: 'Ignored', width: 90, align: 'right', value: (r) => summarizeRun(r).ignored },
      { key: 'new_count', header: 'New', width: 80, align: 'right', value: (r) => r.new_count },
      { key: 'removed_count', header: 'Removed', width: 90, align: 'right', value: (r) => r.removed_count },
      {
        key: 'error',
        header: 'Error',
        minWidth: 160,
        sortable: false,
        value: (r) => r.error,
        render: (r) =>
          r.error ? (
            <Tooltip title={r.error}>
              <Typography variant="body2" color="error.main" noWrap sx={{ maxWidth: 200 }}>
                {r.error}
              </Typography>
            </Tooltip>
          ) : (
            <Typography variant="body2" color="text.disabled">
              —
            </Typography>
          ),
      },
      {
        key: 'actions',
        header: '',
        width: 60,
        align: 'right',
        sortable: false,
        hideable: false,
        render: (r) => (
          <Box onClick={(e) => e.stopPropagation()}>
            {r.status === 'running' ? (
              <Tooltip title="Stop this run">
                <IconButton size="small" onClick={() => cancelM.mutate({ dealerId: r.dealer_id })}>
                  <StopCircleRoundedIcon sx={{ fontSize: 17 }} />
                </IconButton>
              </Tooltip>
            ) : r.status === 'failed' ? (
              <Tooltip title={r.dealer_active ? 'Retry' : 'Dealership is inactive'}>
                <span>
                  <IconButton
                    size="small"
                    disabled={!r.dealer_active || retryM.isPending}
                    onClick={() => retryM.mutate(r.dealer_id)}
                  >
                    <ReplayRoundedIcon sx={{ fontSize: 17 }} />
                  </IconButton>
                </span>
              </Tooltip>
            ) : null}
          </Box>
        ),
      },
    ],
    [cancelM, retryM, now],
  );

  return (
    <Box>
      <PageHeader
        title="Scraping Jobs"
        description="Live view of every scrape run — currently running, recently completed and failed — across all dealerships."
        actions={
          <Button
            variant="contained"
            startIcon={<BoltRoundedIcon />}
            onClick={runScraper.open}
            disabled={runScraper.isRunning}
          >
            Run Scraper
          </Button>
        }
      />

      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: 'repeat(2, 1fr)', md: 'repeat(4, 1fr)' }, gap: 2, mb: 3 }}>
        <AppearIn index={0}>
          <StatCard
            label="Running now"
            value={stats.running}
            icon={<BoltRoundedIcon />}
            color={theme.palette.primary.main}
            deltaCaption={stats.running > 0 ? 'scraping live' : 'idle'}
            loading={runsAgg.isLoading}
          />
        </AppearIn>
        <AppearIn index={1}>
          <StatCard
            label="Completed"
            value={stats.completed}
            icon={<CheckCircleRoundedIcon />}
            color={theme.palette.success.main}
            deltaCaption="successful runs"
            loading={runsAgg.isLoading}
          />
        </AppearIn>
        <AppearIn index={2}>
          <StatCard
            label="Failed"
            value={stats.failed}
            icon={<ErrorOutlineRoundedIcon />}
            color={theme.palette.error.main}
            alert={stats.failed > 0}
            deltaCaption="need attention"
            loading={runsAgg.isLoading}
          />
        </AppearIn>
        <AppearIn index={3}>
          <StatCard
            label="Avg duration"
            value={stats.avgDuration >= 60 ? Math.round(stats.avgDuration / 60) : Math.round(stats.avgDuration)}
            suffix={stats.avgDuration >= 60 ? 'm' : 's'}
            icon={<TimerRoundedIcon />}
            color={chartSeries[3]}
            deltaCaption={`${stats.successRate.toFixed(0)}% success rate`}
            loading={runsAgg.isLoading}
          />
        </AppearIn>
      </Box>

      {running.length > 0 && (
        <AppearIn>
          <SectionCard
            title="Running now"
            description={`${running.length} dealership${running.length === 1 ? '' : 's'} currently scraping`}
            sx={{ mb: 3 }}
          >
            <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: 'repeat(2, 1fr)' }, gap: 1.5, pt: 1 }}>
              {running.map((run) => {
                const pct = run.reported_total ? Math.min(100, percent(run.scraped_count, run.reported_total)) : null;
                return (
                  <Surface
                    key={run.id}
                    tone="sunken"
                    interactive
                    onClick={() => navigate(`/dealers/${run.dealer_id}`)}
                    sx={{ p: 2 }}
                  >
                    <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
                      <Typography variant="body2" sx={{ fontWeight: 600 }} noWrap>
                        {run.dealer_name}
                      </Typography>
                      <StatusChip status="running" />
                    </Box>
                    <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
                      {run.scraped_count.toLocaleString()}
                      {run.reported_total ? ` / ${run.reported_total.toLocaleString()}` : ''} vehicles
                      {run.new_count ? ` · ${run.new_count} new` : ''}
                    </Typography>
                    <LinearProgress
                      variant={pct != null ? 'determinate' : 'indeterminate'}
                      value={pct ?? undefined}
                    />
                  </Surface>
                );
              })}
            </Box>
          </SectionCard>
        </AppearIn>
      )}

      <FilterBar
        activeCount={activeFilterCount}
        onReset={() => {
          setSearch('');
          setStatus('');
          setDealerId('');
        }}
      >
        <SearchField value={search} onChange={setSearch} placeholder="Search dealership…" aria-label="Search jobs" />
        <FilterSelect
          label="Status"
          value={status}
          onChange={setStatus}
          width={140}
          options={[
            { value: 'running', label: 'Running' },
            // The value must be the run status the API actually returns
            // ('completed'); 'success' is the dealer's last_sync_status
            // vocabulary and matched no run, so the filter returned nothing.
            { value: 'completed', label: 'Completed' },
            { value: 'failed', label: 'Failed' },
            { value: 'incomplete', label: 'Incomplete' },
            { value: 'cancelled', label: 'Cancelled' },
          ]}
        />
        <FilterSelect
          label="Dealer"
          value={dealerId}
          onChange={setDealerId}
          width={180}
          options={(dealersQ.data ?? []).map((d) => ({ value: String(d.id), label: d.name }))}
        />
      </FilterBar>

      <SectionCard flush>
        <DataTable
          rows={filtered}
          columns={columns}
          getRowId={(r) => r.id}
          loading={runsAgg.isLoading}
          onRowClick={(r) => navigate(`/dealers/${r.dealer_id}`)}
          page={0}
          pageSize={50}
          pageSizeOptions={[50, 100]}
          storageKey="jobs"
          rowNoun="runs"
          maxHeight="max(520px, calc(100vh - 300px))"
          empty={
            (dealersQ.data ?? []).length === 0 ? (
              <EmptyState
                art="dealers"
                title="No dealerships to monitor"
                description="Add a dealership first — its scrape runs will appear here."
                action={
                  <Button variant="contained" onClick={() => navigate('/dealers/new')}>
                    Add dealership
                  </Button>
                }
              />
            ) : activeFilterCount > 0 ? (
              <EmptyState
                art="filter"
                title="No runs match these filters"
                action={
                  <Button
                    variant="outlined"
                    onClick={() => {
                      setSearch('');
                      setStatus('');
                      setDealerId('');
                    }}
                  >
                    Clear filters
                  </Button>
                }
              />
            ) : (
              <EmptyState
                art="jobs"
                title="No scrape runs yet"
                description="Dispatch a scrape to start building execution history."
                action={
                  <Button variant="contained" startIcon={<BoltRoundedIcon />} onClick={runScraper.open}>
                    Run scraper
                  </Button>
                }
              />
            )
          }
        />
      </SectionCard>
    </Box>
  );
}
