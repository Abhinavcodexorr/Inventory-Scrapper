import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Box, Button, Tab, Tabs, Tooltip, Typography } from '@mui/material';
import { useTheme } from '@mui/material/styles';
import { BarChart } from '@mui/x-charts/BarChart';
import FiberNewRoundedIcon from '@mui/icons-material/FiberNewRounded';
import RemoveCircleOutlineRoundedIcon from '@mui/icons-material/RemoveCircleOutlineRounded';
import SellRoundedIcon from '@mui/icons-material/SellRounded';
import PriceChangeRoundedIcon from '@mui/icons-material/PriceChangeRounded';
import { listChanges, getChangeSummary } from '../api/changes';
import { getErrorMessage } from '../api/client';
import {
  AppearIn,
  ChartFrame,
  DataTable,
  EmptyState,
  FilterBar,
  FilterSelect,
  PageHeader,
  SectionCard,
  StatCard,
  StatusChip,
  useChartSx,
  type Column,
} from '../components/ui';
import { aggregateByDate, useAllTrends, useDashboardSummary, useDealerList } from '../hooks/useDealers';
import { useTokens } from '../theme/useTokens';
import { chartSeries } from '../theme/tokens';
import { formatAxisDate, formatDate, formatMoney, formatRelative } from '../utils/format';
import { useSnackbar } from 'notistack';
import type { Change } from '../types';

type ChangeTab = 'added' | 'removed' | 'sold' | 'price';

/** Tab -> the audit event it shows. The feed reports what CHANGED in a window,
 *  which is not the same as filtering vehicles by their current status: a car
 *  removed six months ago is still `removed` today, but it did not change
 *  last night, and an operator reviewing a sync only cares about the latter. */
const TAB_EVENT: Record<ChangeTab, string> = {
  added: 'created',
  removed: 'removed',
  sold: 'sold',
  price: 'price_changed',
};

const TREND_DAYS = 30;
const PAGE_SIZE = 25;

/** Look-back windows. 24h is the default because the question this page
 *  answers is "what happened in last night's sync?". */
const WINDOWS = [
  { value: '24', label: 'Last 24 hours' },
  { value: '72', label: 'Last 3 days' },
  { value: '168', label: 'Last 7 days' },
  { value: '720', label: 'Last 30 days' },
];

export function ChangesPage() {
  const navigate = useNavigate();
  const theme = useTheme();
  const tk = useTokens();
  const chartSx = useChartSx();
  const { enqueueSnackbar } = useSnackbar();

  // Opened from a dashboard card, ?tab= says which number was clicked.
  // Kept in the URL so the view can be linked to or reloaded intact.
  const [searchParams, setSearchParams] = useSearchParams();
  const urlTab = searchParams.get('tab') as ChangeTab | null;
  const [tab, setTabState] = useState<ChangeTab>(
    urlTab && urlTab in TAB_EVENT ? urlTab : 'added',
  );
  const setTab = (next: ChangeTab) => {
    setTabState(next);
    setSearchParams(next === 'added' ? {} : { tab: next }, { replace: true });
  };
  const [dealerId, setDealerId] = useState('');
  const [hours, setHours] = useState('24');
  const [page, setPage] = useState(0);

  const dealersQ = useDealerList();
  const summaryQ = useDashboardSummary();
  const trends = useAllTrends(dealersQ.data, TREND_DAYS, Boolean(dealersQ.data?.length));
  const daily = useMemo(() => aggregateByDate(trends.snapshots), [trends.snapshots]);

  useEffect(() => setPage(0), [tab, dealerId, hours]);

  const listQ = useQuery({
    queryKey: ['changes', tab, dealerId, hours, page],
    queryFn: () =>
      listChanges({
        event_type: TAB_EVENT[tab],
        hours: Number(hours),
        dealer_id: dealerId ? Number(dealerId) : undefined,
        page: page + 1,
        page_size: PAGE_SIZE,
      }),
    placeholderData: (prev) => prev,
  });

  // Counts for the window on screen. Separate from the dashboard's
  // "today" figures so the tab badges always match the rows below them.
  const windowQ = useQuery({
    queryKey: ['changes', 'summary', dealerId, hours],
    queryFn: () =>
      getChangeSummary({
        hours: Number(hours),
        dealer_id: dealerId ? Number(dealerId) : undefined,
      }),
  });

  useEffect(() => {
    if (listQ.isError) enqueueSnackbar(getErrorMessage(listQ.error), { variant: 'error' });
  }, [listQ.isError, listQ.error, enqueueSnackbar]);

  const columns: Column<Change>[] = useMemo(
    () => [
      {
        key: 'occurred_at',
        header: 'When',
        width: 140,
        value: (c) => c.occurred_at,
        render: (c) => (
          <Tooltip title={formatDate(c.occurred_at)}>
            <Typography variant="body2" color="text.secondary">
              {formatRelative(c.occurred_at)}
            </Typography>
          </Tooltip>
        ),
      },
      {
        key: 'vehicle',
        header: 'Vehicle',
        minWidth: 220,
        hideable: false,
        sortable: false,
        render: (c) => (
          <Box>
            <Typography variant="body2" sx={{ fontWeight: 540 }} noWrap>
              {[c.year, c.make, c.model, c.trim].filter(Boolean).join(' ') || `Vehicle #${c.car_id}`}
            </Typography>
            {/* Stock number and VIN are how the operator identifies this exact
                car against the dealer's own system — without them a row is
                just a description that could match several vehicles. */}
            <Typography variant="caption" color="text.secondary" noWrap>
              {[c.external_id && `Stock ${c.external_id}`, c.vin].filter(Boolean).join(' · ') || '—'}
            </Typography>
          </Box>
        ),
      },
      {
        key: 'dealer_name',
        header: 'Dealer',
        width: 150,
        sortable: false,
        value: (c) => c.dealer_name,
      },
      {
        key: 'change',
        header: 'Change',
        minWidth: 190,
        sortable: false,
        render: (c) => {
          if (c.old_value == null || c.new_value == null) {
            return <StatusChip status={c.event_type.replace('_', ' ')} />;
          }
          const delta = Number(c.delta ?? 0);
          const dropped = delta < 0;
          return (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <Typography variant="body2" color="text.secondary"
                          sx={{ textDecoration: 'line-through' }}>
                {formatMoney(c.old_value)}
              </Typography>
              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                {formatMoney(c.new_value)}
              </Typography>
              <Typography
                variant="caption"
                sx={{
                  fontWeight: 600,
                  // Green for a drop: on a competitor's lot a falling price is
                  // the signal worth reacting to, not a loss.
                  color: dropped ? theme.palette.success.main : theme.palette.warning.main,
                }}
              >
                {dropped ? '' : '+'}{formatMoney(String(delta))}
              </Typography>
            </Box>
          );
        },
      },
    ],
    [theme],
  );

  const w = windowQ.data;
  const tabMeta: Record<ChangeTab, { label: string; count?: number; emptyTitle: string }> = {
    added: { label: 'Added', count: w?.added, emptyTitle: 'No vehicles added in this period' },
    removed: { label: 'Removed', count: w?.removed, emptyTitle: 'No vehicles removed in this period' },
    sold: { label: 'Sold', count: w?.sold, emptyTitle: 'No vehicles marked sold in this period' },
    price: { label: 'Price changes', count: w?.price_changed,
             emptyTitle: 'No price changes in this period' },
  };

  const today = daily.at(-1);

  return (
    <Box>
      <PageHeader
        title="Vehicle Changes"
        description="Daily inventory movement across every dealership — new listings, removals, sales and price activity."
      />

      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: 'repeat(2, 1fr)', md: 'repeat(4, 1fr)' }, gap: 2, mb: 3 }}>
        <AppearIn index={0}>
          <StatCard
            label="Added today"
            value={summaryQ.data?.added_today ?? 0}
            icon={<FiberNewRoundedIcon />}
            color={theme.palette.success.main}
            series={daily.map((d) => d.added)}
            loading={summaryQ.isLoading}
            onClick={() => setTab('added')}
          />
        </AppearIn>
        <AppearIn index={1}>
          <StatCard
            label="Removed today"
            value={summaryQ.data?.removed_today ?? 0}
            icon={<RemoveCircleOutlineRoundedIcon />}
            color={chartSeries[4]}
            series={daily.map((d) => d.removed)}
            loading={summaryQ.isLoading}
            onClick={() => setTab('removed')}
          />
        </AppearIn>
        <AppearIn index={2}>
          <StatCard
            label="Sold today"
            value={summaryQ.data?.sold_today ?? 0}
            icon={<SellRoundedIcon />}
            color={theme.palette.info.main}
            series={daily.map((d) => d.sold)}
            loading={summaryQ.isLoading}
            onClick={() => setTab('sold')}
          />
        </AppearIn>
        <AppearIn index={3}>
          <StatCard
            label="Price changes"
            value={daily.reduce((sum, d) => sum + d.priceChanged, 0)}
            icon={<PriceChangeRoundedIcon />}
            color={chartSeries[3]}
            deltaCaption={`last ${TREND_DAYS} days`}
            series={daily.map((d) => d.priceChanged)}
            loading={trends.isLoading}
            onClick={() => setTab('price')}
          />
        </AppearIn>
      </Box>

      <AppearIn>
        <SectionCard
          title="Inventory movement"
          description={`Vehicles added vs removed, last ${TREND_DAYS} days`}
          sx={{ mb: 3 }}
        >
          <ChartFrame
            loading={trends.isLoading}
            isEmpty={daily.length === 0}
            height={280}
            emptyTitle="No movement recorded yet"
            emptyDescription="This chart fills in once daily inventory snapshots start accumulating."
          >
            <BarChart
              height={280}
              margin={{ left: 8, right: 12, top: 24, bottom: 24 }}
              grid={{ horizontal: true }}
              sx={chartSx}
              series={[
                { data: daily.map((d) => d.added), label: 'Added', color: theme.palette.success.main },
                { data: daily.map((d) => d.removed), label: 'Removed', color: theme.palette.text.secondary },
                { data: daily.map((d) => d.sold), label: 'Sold', color: theme.palette.info.main },
              ]}
              xAxis={[
                {
                  scaleType: 'band',
                  data: daily.map((d) => d.date),
                  valueFormatter: (v: string) => formatAxisDate(v),
                },
              ]}
            />
          </ChartFrame>
          {today && (
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
              Latest snapshot: {formatDate(today.date)}
            </Typography>
          )}
        </SectionCard>
      </AppearIn>

      <Tabs value={tab} onChange={(_, v: ChangeTab) => setTab(v)} sx={{ mb: 2, borderBottom: `1px solid ${tk.surfaces.border}` }}>
        {(Object.keys(tabMeta) as ChangeTab[]).map((key) => (
          <Tab
            key={key}
            value={key}
            label={
              tabMeta[key].count === undefined
                ? tabMeta[key].label
                : `${tabMeta[key].label} (${tabMeta[key].count})`
            }
          />
        ))}
      </Tabs>

      <FilterBar
        activeCount={(dealerId ? 1 : 0) + (hours !== '24' ? 1 : 0)}
        onReset={() => {
          setDealerId('');
          setHours('24');
        }}
      >
        <FilterSelect
          label="Period"
          value={hours}
          onChange={setHours}
          width={180}
          options={WINDOWS}
        />
        <FilterSelect
          label="Dealer"
          value={dealerId}
          onChange={setDealerId}
          width={200}
          options={(dealersQ.data ?? []).map((d) => ({ value: String(d.id), label: d.name }))}
        />
      </FilterBar>

      <SectionCard flush>
        <DataTable
          rows={listQ.data?.items ?? []}
          columns={columns}
          getRowId={(c) => c.id}
          loading={listQ.isLoading}
          onRowClick={(c) => navigate(`/vehicles/${c.car_id}`)}
          page={page}
          pageSize={PAGE_SIZE}
          rowCount={listQ.data?.total ?? 0}
          onPageChange={setPage}
          pageSizeOptions={[25, 50, 100]}
          rowNoun="changes"
          empty={
            <EmptyState
              art="vehicles"
              title={tabMeta[tab].emptyTitle}
              description="Changes will appear here as dealerships sync."
              action={
                <Button variant="outlined" onClick={() => navigate('/dealers')}>
                  Go to dealerships
                </Button>
              }
            />
          }
        />
      </SectionCard>
    </Box>
  );
}
