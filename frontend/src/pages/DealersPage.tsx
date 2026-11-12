import { useEffect, useMemo, useState } from 'react';
import { Link as RouterLink, useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { isAxiosError } from 'axios';
import {
  Alert,
  Avatar,
  Box,
  Button,
  CircularProgress,
  Divider,
  IconButton,
  ListItemIcon,
  Menu,
  MenuItem,
  Pagination,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from '@mui/material';
import { alpha, useTheme } from '@mui/material/styles';
import AddRoundedIcon from '@mui/icons-material/AddRounded';
import UploadFileRoundedIcon from '@mui/icons-material/UploadFileRounded';
import HistoryRoundedIcon from '@mui/icons-material/HistoryRounded';
import SyncRoundedIcon from '@mui/icons-material/SyncRounded';
import EditRoundedIcon from '@mui/icons-material/EditRounded';
import MoreHorizRoundedIcon from '@mui/icons-material/MoreHorizRounded';
import DeleteOutlineRoundedIcon from '@mui/icons-material/DeleteOutlineRounded';
import ToggleOnRoundedIcon from '@mui/icons-material/ToggleOnRounded';
import ToggleOffRoundedIcon from '@mui/icons-material/ToggleOffRounded';
import OpenInNewRoundedIcon from '@mui/icons-material/OpenInNew';
import GridViewRoundedIcon from '@mui/icons-material/GridViewRounded';
import ViewListRoundedIcon from '@mui/icons-material/ViewListRounded';
import { useSnackbar } from 'notistack';
import {
  activateDealer,
  deactivateDealer,
  deleteDealer,
  listProviders,
  syncDealer,
} from '../api/dealers';
import { getErrorMessage } from '../api/client';
import {
  AppearIn,
  ConfirmDialog,
  DataTable,
  EmptyState,
  FilterBar,
  FilterSelect,
  PageHeader,
  SearchField,
  SectionCard,
  StatusChip,
  Surface,
  type Column,
  type SortState,
} from '../components/ui';
import { useDashboardSummary, useDealerPage, useSuccessTally } from '../hooks/useDealers';
import { useBulkActivity } from '../hooks/useBulkActivity';
import { useDebouncedValue } from '../utils/useDebouncedValue';
import { useTokens } from '../theme/useTokens';
import { useAppSettings } from '../theme/settings';
import { chartSeries } from '../theme/tokens';
import {
  colorForName,
  formatDate,
  formatDateShort,
  formatRelative,
  formatSeconds,
  initials,
  percent,
} from '../utils/format';
import type { Dealer } from '../types';
import { isRunSuccess } from '../utils/runStatus';

type View = 'grid' | 'table';

const DEFAULT_SORT: SortState = { key: 'created_at', dir: 'desc' };

const SORT_OPTIONS = [
  { value: 'created_at:desc', label: 'Newest added' },
  { value: 'created_at:asc', label: 'Oldest added' },
  { value: 'name:asc', label: 'Name A–Z' },
  { value: 'name:desc', label: 'Name Z–A' },
  { value: 'last_synced_at:desc', label: 'Recently synced' },
  { value: 'inventory_count:desc', label: 'Most vehicles' },
  { value: 'inventory_count:asc', label: 'Fewest vehicles' },
];

const SYNC_OPTIONS = [
  { value: 'failed', label: 'Failed to scrape' },
  { value: 'incomplete', label: 'Incomplete' },
  { value: 'no_vehicles', label: 'Synced, 0 vehicles' },
  { value: 'never', label: 'Never synced' },
  { value: 'success', label: 'Succeeded' },
];

const ADDED_OPTIONS = [
  { value: '1', label: 'Last 24 hours' },
  { value: '7', label: 'Last 7 days' },
  { value: '30', label: 'Last 30 days' },
  { value: '90', label: 'Last 90 days' },
];
const VIEW_STORAGE_KEY = 'inventory-hub.dealers.view';

export function DealersPage() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { settings } = useAppSettings();
  const { enqueueSnackbar } = useSnackbar();

  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('');
  const [provider, setProvider] = useState('');
  const [syncStatus, setSyncStatus] = useState('');
  const [added, setAdded] = useState('');
  const [page, setPage] = useState(0);
  const [view, setView] = useState<View>(
    () => (localStorage.getItem(VIEW_STORAGE_KEY) as View | null) ?? 'grid',
  );

  const [deleteTarget, setDeleteTarget] = useState<Dealer | null>(null);
  const [deleteBlockedMsg, setDeleteBlockedMsg] = useState<string | null>(null);
  const [menuFor, setMenuFor] = useState<{ anchor: HTMLElement; dealer: Dealer } | null>(null);

  const debouncedSearch = useDebouncedValue(search);

  useEffect(() => localStorage.setItem(VIEW_STORAGE_KEY, view), [view]);
  useEffect(() => setPage(0), [debouncedSearch, status, provider, syncStatus, added]);

  const providersQ = useQuery({ queryKey: ['providers'], queryFn: listProviders });
  const [sort, setSort] = useState<SortState>(DEFAULT_SORT);
  useEffect(() => setPage(0), [sort]);

  const pageSize = settings.pageSize;
  const dealersQ = useDealerPage({
    q: debouncedSearch.trim() || undefined,
    active: status ? status === 'active' : undefined,
    provider: provider || undefined,
    sync_status: syncStatus || undefined,
    added_within_days: added ? Number(added) : undefined,
    sort: sort.key,
    order: sort.dir,
    limit: pageSize,
    offset: page * pageSize,
  });
  const rows = useMemo(() => dealersQ.data?.items ?? [], [dealersQ.data]);
  const total = dealersQ.data?.total ?? 0;
  const summary = useDashboardSummary().data;

  const rowIds = useMemo(() => rows.map((d) => d.id), [rows]);
  const successByDealer = useSuccessTally(rowIds, isRunSuccess);

  useEffect(() => {
    if (dealersQ.isError) enqueueSnackbar(getErrorMessage(dealersQ.error), { variant: 'error' });
  }, [dealersQ.isError, dealersQ.error, enqueueSnackbar]);

  const activeFilterCount = [debouncedSearch, status, provider, syncStatus, added].filter(Boolean).length;
  const sortValue = `${sort.key}:${sort.dir}`;
  const sortOptions = SORT_OPTIONS.some((o) => o.value === sortValue)
    ? SORT_OPTIONS
    : [...SORT_OPTIONS, { value: sortValue, label: 'Table column' }];

  const invalidateDealers = () => {
    qc.invalidateQueries({ queryKey: ['dealers'] });
    qc.invalidateQueries({ queryKey: ['dashboard'] });
  };

  const activateM = useMutation({
    mutationFn: activateDealer,
    onSuccess: () => {
      enqueueSnackbar('Dealership activated', { variant: 'success' });
      invalidateDealers();
    },
    onError: (e) => enqueueSnackbar(getErrorMessage(e), { variant: 'error' }),
  });

  const deactivateM = useMutation({
    mutationFn: deactivateDealer,
    onSuccess: () => {
      enqueueSnackbar('Dealership deactivated', { variant: 'success' });
      invalidateDealers();
    },
    onError: (e) => enqueueSnackbar(getErrorMessage(e), { variant: 'error' }),
  });

  const syncM = useMutation({
    mutationFn: syncDealer,
    onSuccess: (job) => {
      enqueueSnackbar(
        job.status === 'running'
          ? 'A sync is already running for this dealership'
          : `Sync started (${job.mode === 'local' ? 'in-process' : 'queued'})`,
        { variant: job.status === 'running' ? 'info' : 'success' },
      );
      qc.invalidateQueries({ queryKey: ['dealers'] });
      qc.invalidateQueries({ queryKey: ['dealer', job.dealer_id] });
      qc.invalidateQueries({ queryKey: ['vehicles'] });
      qc.invalidateQueries({ queryKey: ['dashboard'] });
    },
    onError: (e) => enqueueSnackbar(getErrorMessage(e), { variant: 'error' }),
  });

  // A blocked delete (409) keeps the dialog open and escalates to the explicit
  // "delete everything" path rather than dead-ending the user on a toast.
  const deleteM = useMutation({
    mutationFn: ({ id, purge }: { id: number; purge: boolean }) => deleteDealer(id, purge),
    onSuccess: () => {
      enqueueSnackbar('Dealership deleted', { variant: 'success' });
      setDeleteTarget(null);
      setDeleteBlockedMsg(null);
      invalidateDealers();
    },
    onError: (e) => {
      if (isAxiosError(e) && e.response?.status === 409) {
        setDeleteBlockedMsg(getErrorMessage(e));
        return;
      }
      enqueueSnackbar(getErrorMessage(e), { variant: 'error' });
    },
  });

  const providerOptions = useMemo(
    () => (providersQ.data ?? []).map((p) => ({ value: p, label: p })),
    [providersQ.data],
  );

  const columns: Column<Dealer>[] = useMemo(
    () => [
      {
        key: 'name',
        header: 'Dealership',
        minWidth: 220,
        hideable: false,
        value: (d) => d.name,
        render: (d) => (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, minWidth: 0 }}>
            <DealerAvatar name={d.name} size={30} />
            <Box sx={{ minWidth: 0 }}>
              <Typography variant="body2" noWrap sx={{ fontWeight: 560 }}>
                {d.name}
              </Typography>
              <Typography variant="caption" color="text.secondary" noWrap sx={{ display: 'block' }}>
                {d.base_url.replace(/^https?:\/\//, '')}
              </Typography>
            </Box>
          </Box>
        ),
      },
      {
        key: 'website_provider',
        header: 'Provider',
        width: 130,
        value: (d) => d.website_provider,
        render: (d) =>
          d.website_provider ? (
            <Typography variant="body2">{d.website_provider}</Typography>
          ) : (
            <Typography variant="body2" color="text.disabled">
              —
            </Typography>
          ),
      },
      {
        key: 'location',
        header: 'Location',
        width: 150,
        sortable: false,
        value: (d) => [d.city, d.province].filter(Boolean).join(', '),
        render: (d) => (
          <Typography variant="body2" color={d.city ? 'text.primary' : 'text.disabled'}>
            {[d.city, d.province].filter(Boolean).join(', ') || '—'}
          </Typography>
        ),
      },
      {
        key: 'inventory_count',
        header: 'Inventory',
        width: 110,
        align: 'right',
        value: (d) => d.inventory_count,
        render: (d) => (
          <Typography variant="body2" sx={{ fontWeight: 600 }}>
            {d.inventory_count.toLocaleString()}
          </Typography>
        ),
      },
      {
        key: 'success',
        header: 'Success rate',
        width: 130,
        sortable: false,
        hint: 'Share of recent runs that completed successfully',
        value: (d) => {
          const s = successByDealer.get(d.id);
          return s?.total ? percent(s.ok, s.total) : null;
        },
        render: (d) => <SuccessMeter tally={successByDealer.get(d.id)} />,
      },
      {
        key: 'last_sync_duration_seconds',
        header: 'Duration',
        width: 100,
        align: 'right',
        hint: 'How long the last scrape took',
        value: (d) => d.last_sync_duration_seconds,
        render: (d) => (
          <Typography variant="body2" color="text.secondary">
            {formatSeconds(d.last_sync_duration_seconds)}
          </Typography>
        ),
      },
      {
        key: 'last_synced_at',
        header: 'Last sync',
        width: 150,
        value: (d) => d.last_synced_at,
        render: (d) => (
          <Box>
            <Typography variant="body2">{formatRelative(d.last_synced_at)}</Typography>
            {d.last_sync_status && (
              <Typography variant="caption" color="text.secondary" sx={{ textTransform: 'capitalize' }}>
                {d.last_sync_status}
              </Typography>
            )}
          </Box>
        ),
      },
      {
        key: 'active',
        header: 'Status',
        width: 110,
        value: (d) => (d.active ? 'active' : 'inactive'),
        render: (d) => <StatusChip status={d.active ? 'active' : 'inactive'} />,
      },
      {
        // Dealer.created_at: stamped once, when this dealership was added to the tool.
        // Sorting reuses the server's created_at sort.
        key: 'created_at',
        header: 'Created Date',
        width: 130,
        hint: 'The date this dealership was added to the tool',
        value: (d) => d.created_at,
        render: (d) => (
          <Tooltip title={`Added ${formatRelative(d.created_at)} · ${formatDate(d.created_at)}`}>
            <Typography variant="body2" sx={{ fontVariantNumeric: 'tabular-nums' }}>
              {formatDateShort(d.created_at)}
            </Typography>
          </Tooltip>
        ),
      },
      {
        key: 'actions',
        header: '',
        width: 96,
        sortable: false,
        hideable: false,
        align: 'right',
        render: (d) => (
          <Box sx={{ display: 'flex', justifyContent: 'flex-end', gap: 0.25 }} onClick={(e) => e.stopPropagation()}>
            <Tooltip title={d.active ? 'Sync now' : 'Activate to enable syncing'}>
              <span>
                <IconButton size="small" disabled={!d.active || syncM.isPending} onClick={() => syncM.mutate(d.id)}>
                  <SyncRoundedIcon sx={{ fontSize: 17 }} />
                </IconButton>
              </span>
            </Tooltip>
            <Tooltip title="More actions">
              <IconButton size="small" onClick={(e) => setMenuFor({ anchor: e.currentTarget, dealer: d })}>
                <MoreHorizRoundedIcon sx={{ fontSize: 17 }} />
              </IconButton>
            </Tooltip>
          </Box>
        ),
      },
    ],
    [successByDealer, syncM],
  );

  const showEmptyState = !dealersQ.isLoading && total === 0;
  const bulk = useBulkActivity().data;
  const bulkBusy = bulk ? bulk.checking + bulk.waiting > 0 : false;

  return (
    <Box>
      <PageHeader
        title="Dealerships"
        description="Every website the platform scrapes for inventory. Add a source, verify its scraper, then let it sync automatically."
        eyebrow={
          summary
            ? `${summary.dealers_total.toLocaleString()} source${summary.dealers_total === 1 ? '' : 's'} · ${summary.dealers_active.toLocaleString()} active`
            : undefined
        }
        actions={
          <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
            <Button variant="outlined" component={RouterLink} to="/dealers/imports" startIcon={<HistoryRoundedIcon />}>
              Past imports{bulkBusy ? ` (${(bulk!.checking + bulk!.waiting).toLocaleString()} left)` : ''}
            </Button>
            <Button variant="outlined" component={RouterLink} to="/dealers/bulk" startIcon={<UploadFileRoundedIcon />}>
              Bulk add
            </Button>
            <Button variant="contained" component={RouterLink} to="/dealers/new" startIcon={<AddRoundedIcon />}>
              Add dealership
            </Button>
          </Box>
        }
      />

      {bulkBusy && (
        <Alert
          severity="info"
          icon={<CircularProgress size={18} />}
          sx={{ mb: 2 }}
          action={
            <Button color="inherit" size="small" component={RouterLink} to="/dealers/imports">
              View imports
            </Button>
          }
        >
          Bulk import running: <strong>{bulk!.checking.toLocaleString()}</strong> website
          {bulk!.checking === 1 ? '' : 's'} in process, <strong>{bulk!.waiting.toLocaleString()}</strong> waiting
          {bulk!.running_imports > 1 ? ` across ${bulk!.running_imports} lists` : ''}.
        </Alert>
      )}

      <FilterBar
        activeCount={activeFilterCount}
        onReset={() => {
          setSearch('');
          setStatus('');
          setProvider('');
          setSyncStatus('');
          setAdded('');
          setSort(DEFAULT_SORT);
        }}
        trailing={
          <ToggleButtonGroup
            exclusive
            size="small"
            value={view}
            onChange={(_, next: View | null) => next && setView(next)}
            aria-label="View mode"
          >
            <ToggleButton value="grid" aria-label="Card view">
              <GridViewRoundedIcon sx={{ fontSize: 17 }} />
            </ToggleButton>
            <ToggleButton value="table" aria-label="Table view">
              <ViewListRoundedIcon sx={{ fontSize: 17 }} />
            </ToggleButton>
          </ToggleButtonGroup>
        }
      >
        <SearchField
          value={search}
          onChange={setSearch}
          placeholder="Search name, website, city…"
          aria-label="Search dealerships"
        />
        <FilterSelect
          label="Status"
          value={status}
          onChange={setStatus}
          width={132}
          options={[
            { value: 'active', label: 'Active' },
            { value: 'inactive', label: 'Inactive' },
          ]}
        />
        <FilterSelect label="Provider" value={provider} onChange={setProvider} width={172} options={providerOptions} />
        <FilterSelect label="Last sync" value={syncStatus} onChange={setSyncStatus} width={178} options={SYNC_OPTIONS} />
        <FilterSelect label="Added" value={added} onChange={setAdded} width={150} allLabel="Any time" options={ADDED_OPTIONS} />
        <FilterSelect
          label="Sort by"
          value={sortValue}
          onChange={(v) => {
            const [key, dir] = v.split(':');
            setSort({ key, dir: dir as SortState['dir'] });
          }}
          width={168}
          required
          options={sortOptions}
        />
      </FilterBar>

      {dealersQ.isError && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {getErrorMessage(dealersQ.error)}
        </Alert>
      )}

      {showEmptyState ? (
        <SectionCard>
          {activeFilterCount === 0 ? (
            <EmptyState
              art="dealers"
              title="No Dealers Yet"
              description="Add your first dealership to begin collecting inventory automatically."
              action={
                <Button variant="contained" component={RouterLink} to="/dealers/new" startIcon={<AddRoundedIcon />}>
                  Add Dealer
                </Button>
              }
            />
          ) : (
            <EmptyState
              art="filter"
              title="No dealerships match these filters"
              description="Try a broader search, or clear the filters to see every source."
              action={
                <Button
                  variant="outlined"
                  onClick={() => {
                    setSearch('');
                    setStatus('');
                    setProvider('');
                  }}
                >
                  Clear filters
                </Button>
              }
            />
          )}
        </SectionCard>
      ) : view === 'grid' ? (
        <>
        <Box
          sx={{
            display: 'grid',
            gridTemplateColumns: {
              xs: '1fr',
              sm: 'repeat(2, minmax(0, 1fr))',
              xl: 'repeat(3, minmax(0, 1fr))',
            },
            gap: 2,
          }}
        >
          {dealersQ.isLoading
            ? Array.from({ length: 6 }).map((_, i) => <DealerCardSkeleton key={i} />)
            : rows.map((dealer, i) => (
                <AppearIn key={dealer.id} index={i}>
                  <DealerCard
                    dealer={dealer}
                    tally={successByDealer.get(dealer.id)}
                    syncing={syncM.isPending && syncM.variables === dealer.id}
                    onOpen={() => navigate(`/dealers/${dealer.id}`)}
                    onSync={() => syncM.mutate(dealer.id)}
                    onMenu={(anchor) => setMenuFor({ anchor, dealer })}
                  />
                </AppearIn>
              ))}
        </Box>
        {total > pageSize && (
          <Box sx={{ display: 'flex', justifyContent: 'center', mt: 3 }}>
            <Pagination
              count={Math.ceil(total / pageSize)}
              page={page + 1}
              onChange={(_, next) => setPage(next - 1)}
              shape="rounded"
            />
          </Box>
        )}
        </>
      ) : (
        <SectionCard flush>
          <DataTable
            rows={rows}
            rowCount={total}
            sort={sort}
            onSortChange={setSort}
            columns={columns}
            getRowId={(d) => d.id}
            loading={dealersQ.isLoading}
            onRowClick={(d) => navigate(`/dealers/${d.id}`)}
            page={page}
            pageSize={pageSize}
            onPageChange={setPage}
            pageSizeOptions={[25, 50, 100]}
            storageKey="dealers"
            rowNoun="dealerships"
          />
        </SectionCard>
      )}

      {/* ── Row / card overflow menu ───────────────────────────────────── */}
      <Menu anchorEl={menuFor?.anchor ?? null} open={Boolean(menuFor)} onClose={() => setMenuFor(null)}>
        <MenuItem
          onClick={() => {
            navigate(`/dealers/${menuFor!.dealer.id}/edit`);
            setMenuFor(null);
          }}
        >
          <ListItemIcon>
            <EditRoundedIcon fontSize="small" />
          </ListItemIcon>
          Edit settings
        </MenuItem>
        <MenuItem
          component="a"
          href={menuFor?.dealer.base_url}
          target="_blank"
          rel="noopener noreferrer"
          onClick={() => setMenuFor(null)}
        >
          <ListItemIcon>
            <OpenInNewRoundedIcon fontSize="small" />
          </ListItemIcon>
          Visit website
        </MenuItem>
        <MenuItem
          onClick={() => {
            const d = menuFor!.dealer;
            if (d.active) deactivateM.mutate(d.id);
            else activateM.mutate(d.id);
            setMenuFor(null);
          }}
        >
          <ListItemIcon>
            {menuFor?.dealer.active ? (
              <ToggleOffRoundedIcon fontSize="small" />
            ) : (
              <ToggleOnRoundedIcon fontSize="small" color="success" />
            )}
          </ListItemIcon>
          {menuFor?.dealer.active ? 'Deactivate' : 'Activate'}
        </MenuItem>
        <Divider sx={{ my: 0.5 }} />
        <MenuItem
          onClick={() => {
            setDeleteTarget(menuFor!.dealer);
            setMenuFor(null);
          }}
          sx={{ color: 'error.main' }}
        >
          <ListItemIcon>
            <DeleteOutlineRoundedIcon fontSize="small" color="error" />
          </ListItemIcon>
          Delete
        </MenuItem>
      </Menu>

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        tone="danger"
        title={deleteBlockedMsg ? 'This dealership has inventory' : 'Delete dealership?'}
        description={
          deleteBlockedMsg ?? (
            <>
              Delete <strong>{deleteTarget?.name}</strong>? Deactivating is usually better — it retires the
              dealership while keeping its inventory history.
            </>
          )
        }
        confirmLabel={deleteBlockedMsg ? 'Delete everything' : 'Delete'}
        loading={deleteM.isPending}
        onClose={() => {
          setDeleteTarget(null);
          setDeleteBlockedMsg(null);
        }}
        onConfirm={() =>
          deleteTarget && deleteM.mutate({ id: deleteTarget.id, purge: Boolean(deleteBlockedMsg) })
        }
      >
        {deleteBlockedMsg && (
          <Typography variant="body2" color="error" sx={{ fontWeight: 600 }}>
            This cannot be undone. All vehicles, price history and audit records for {deleteTarget?.name} will be
            permanently removed.
          </Typography>
        )}
      </ConfirmDialog>

    </Box>
  );
}

/** Dealer identity avatar — deterministic colour, initials fallback. */
function DealerAvatar({ name, size = 44 }: { name: string; size?: number }) {
  const theme = useTheme();
  const color = colorForName(name, chartSeries);

  return (
    <Avatar
      variant="rounded"
      sx={{
        width: size,
        height: size,
        fontSize: size * 0.34,
        fontWeight: 700,
        color,
        bgcolor: alpha(color, theme.palette.mode === 'light' ? 0.12 : 0.2),
        boxShadow: `inset 0 0 0 1px ${alpha(color, 0.2)}`,
      }}
    >
      {initials(name)}
    </Avatar>
  );
}

/** Compact bar + percentage for a dealership's recent run outcomes. */
function SuccessMeter({ tally }: { tally?: { ok: number; total: number } }) {
  const theme = useTheme();

  if (!tally?.total) {
    return (
      <Typography variant="body2" color="text.disabled">
        —
      </Typography>
    );
  }

  const rate = percent(tally.ok, tally.total);
  const color =
    rate >= 90 ? theme.palette.success.main : rate >= 60 ? theme.palette.warning.main : theme.palette.error.main;

  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
      <Box
        sx={{
          flex: 1,
          minWidth: 36,
          height: 5,
          borderRadius: 999,
          bgcolor: alpha(color, 0.16),
          overflow: 'hidden',
        }}
      >
        <Box
          sx={{
            width: `${rate}%`,
            height: '100%',
            bgcolor: color,
            borderRadius: 999,
            transition: 'width 600ms cubic-bezier(0.16, 1, 0.3, 1)',
          }}
        />
      </Box>
      <Typography variant="caption" sx={{ fontWeight: 600, color, minWidth: 30, textAlign: 'right' }}>
        {rate.toFixed(0)}%
      </Typography>
    </Box>
  );
}

interface DealerCardProps {
  dealer: Dealer;
  tally?: { ok: number; total: number };
  syncing: boolean;
  onOpen: () => void;
  onSync: () => void;
  onMenu: (anchor: HTMLElement) => void;
}

/** Data-rich dealership card used in the default grid view. */
function DealerCard({ dealer, tally, syncing, onOpen, onSync, onMenu }: DealerCardProps) {
  const tk = useTokens();

  return (
    <Surface
      interactive
      onClick={onOpen}
      sx={{ p: 2.5, height: '100%', display: 'flex', flexDirection: 'column', gap: 2 }}
    >
      <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 1.75 }}>
        <DealerAvatar name={dealer.name} />
        <Box sx={{ minWidth: 0, flex: 1 }}>
          <Typography variant="h6" noWrap title={dealer.name}>
            {dealer.name}
          </Typography>
          <Typography variant="caption" color="text.secondary" noWrap sx={{ display: 'block' }}>
            {dealer.base_url.replace(/^https?:\/\//, '')}
          </Typography>
        </Box>
        <StatusChip status={dealer.active ? 'active' : 'inactive'} />
      </Box>

      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
          gap: 1.5,
          p: 1.75,
          borderRadius: `${tk.radii.control}px`,
          bgcolor: tk.surfaces.sunken,
        }}
      >
        <CardMetric label="Inventory" value={dealer.inventory_count.toLocaleString()} />
        <CardMetric label="Provider" value={dealer.website_provider || 'Unknown'} />
        <CardMetric label="Last sync" value={formatRelative(dealer.last_synced_at)} />
        <CardMetric label="Response time" value={formatSeconds(dealer.last_sync_duration_seconds)} />
      </Box>

      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
        <Typography variant="caption" color="text.secondary" sx={{ flexShrink: 0 }}>
          Success rate
        </Typography>
        <Box sx={{ flex: 1 }}>
          <SuccessMeter tally={tally} />
        </Box>
      </Box>

      <Box sx={{ display: 'flex', gap: 1, mt: 'auto', pt: 0.5 }} onClick={(e) => e.stopPropagation()}>
        <Button size="small" variant="outlined" onClick={onOpen} sx={{ flex: 1 }}>
          Open
        </Button>
        <Button
          size="small"
          variant="outlined"
          disabled={!dealer.active || syncing}
          onClick={onSync}
          startIcon={syncing ? <CircularProgress size={13} color="inherit" /> : <SyncRoundedIcon sx={{ fontSize: 16 }} />}
          sx={{ flex: 1 }}
        >
          {syncing ? 'Starting…' : 'Sync now'}
        </Button>
        <Tooltip title="More actions">
          <IconButton size="small" onClick={(e) => onMenu(e.currentTarget)} aria-label={`Actions for ${dealer.name}`}>
            <MoreHorizRoundedIcon sx={{ fontSize: 18 }} />
          </IconButton>
        </Tooltip>
      </Box>
    </Surface>
  );
}

function CardMetric({ label, value }: { label: string; value: string }) {
  return (
    <Box sx={{ minWidth: 0 }}>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
        {label}
      </Typography>
      <Typography variant="body2" noWrap sx={{ fontWeight: 600 }} title={value}>
        {value}
      </Typography>
    </Box>
  );
}

function DealerCardSkeleton() {
  const tk = useTokens();
  return (
    <Surface sx={{ p: 2.5, height: 268 }}>
      <Box sx={{ display: 'flex', gap: 1.75, mb: 2 }}>
        <Box sx={{ width: 44, height: 44, borderRadius: `${tk.radii.sm + 2}px`, bgcolor: tk.surfaces.sunken }} />
        <Box sx={{ flex: 1 }}>
          <Box sx={{ width: '60%', height: 14, borderRadius: 1, bgcolor: tk.surfaces.sunken, mb: 1 }} />
          <Box sx={{ width: '40%', height: 10, borderRadius: 1, bgcolor: tk.surfaces.sunken }} />
        </Box>
      </Box>
      <Box sx={{ height: 96, borderRadius: `${tk.radii.control}px`, bgcolor: tk.surfaces.sunken, mb: 2 }} />
      <Box sx={{ height: 36, borderRadius: `${tk.radii.control}px`, bgcolor: tk.surfaces.sunken }} />
    </Surface>
  );
}
