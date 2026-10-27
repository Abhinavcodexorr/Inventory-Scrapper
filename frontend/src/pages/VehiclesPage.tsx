import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  Alert,
  Box,
  Button,
  Chip,
  Divider,
  IconButton,
  ListItemIcon,
  Menu,
  MenuItem,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import { alpha, useTheme } from '@mui/material/styles';
import DirectionsCarFilledRoundedIcon from '@mui/icons-material/DirectionsCarFilledRounded';
import BookmarkBorderRoundedIcon from '@mui/icons-material/BookmarkBorderRounded';
import BookmarkAddedRoundedIcon from '@mui/icons-material/BookmarkAddedRounded';
import DeleteOutlineRoundedIcon from '@mui/icons-material/DeleteOutlineRounded';
import FileDownloadRoundedIcon from '@mui/icons-material/FileDownloadRounded';
import ContentCopyRoundedIcon from '@mui/icons-material/ContentCopyRounded';
import { useSnackbar } from 'notistack';
import { listVehicles } from '../api/vehicles';
import { getErrorMessage } from '../api/client';
import {
  DataTable,
  EmptyState,
  FilterBar,
  FilterSelect,
  NumberRange,
  PageHeader,
  SearchField,
  SectionCard,
  StatusChip,
  type Column,
  type SortState,
} from '../components/ui';
import { useDealerList } from '../hooks/useDealers';
import { useDebouncedValue } from '../utils/useDebouncedValue';
import { useAppSettings } from '../theme/settings';
import { useTokens } from '../theme/useTokens';
import { downloadCsv, stampedFilename } from '../utils/csv';
import { formatDate, formatMoney, formatRelative, vehicleTitle } from '../utils/format';
import type { Vehicle } from '../types';

/** Sort keys the API actually understands — everything else falls back to id. */
const SERVER_SORTABLE = new Set(['id', 'price', 'year', 'mileage', 'last_seen_at']);

interface FilterState {
  search: string;
  make: string;
  model: string;
  yearMin: string;
  yearMax: string;
  priceMin: string;
  priceMax: string;
  status: string;
  dealerId: string;
}

/** Default view, and what Reset returns to.
 *
 * `status: 'active'` rather than blank: "Inventory" means cars a dealer is
 * currently selling. Showing every row made this page read 5,868 while the
 * dashboard read 4,372 — both correct, counting different things, and no way
 * for the client to tell which was the real number. The gap appeared the day
 * we began ingesting sold vehicles from feeds that report them (600+ from the
 * Zop dealers alone), and it will only widen as history accumulates.
 *
 * Sold and removed stay one click away via the Status filter.
 */
const EMPTY_FILTERS: FilterState = {
  search: '',
  make: '',
  model: '',
  yearMin: '',
  yearMax: '',
  priceMin: '',
  priceMax: '',
  status: 'active',
  dealerId: '',
};

interface SavedFilter {
  name: string;
  filters: FilterState;
}

const SAVED_FILTERS_KEY = 'inventory-hub.vehicles.savedFilters';

function parseOptionalNumber(raw: string): number | undefined {
  if (raw.trim() === '') return undefined;
  const n = Number(raw);
  return Number.isFinite(n) ? n : undefined;
}

export function VehiclesPage() {
  const navigate = useNavigate();
  const theme = useTheme();
  const { settings } = useAppSettings();
  const { enqueueSnackbar } = useSnackbar();

  const [filters, setFilters] = useState<FilterState>(EMPTY_FILTERS);
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(settings.pageSize);
  const [sort, setSort] = useState<SortState>({ key: 'id', dir: 'desc' });
  const [selected, setSelected] = useState<Set<string | number>>(new Set());

  const [savedFilters, setSavedFilters] = useState<SavedFilter[]>(() => {
    try {
      return JSON.parse(localStorage.getItem(SAVED_FILTERS_KEY) ?? '[]') as SavedFilter[];
    } catch {
      return [];
    }
  });
  const [savedAnchor, setSavedAnchor] = useState<HTMLElement | null>(null);
  const [newFilterName, setNewFilterName] = useState('');

  const set = <K extends keyof FilterState>(key: K, value: FilterState[K]) =>
    setFilters((prev) => ({ ...prev, [key]: value }));

  const debounced = {
    search: useDebouncedValue(filters.search),
    make: useDebouncedValue(filters.make),
    model: useDebouncedValue(filters.model),
    yearMin: useDebouncedValue(filters.yearMin),
    yearMax: useDebouncedValue(filters.yearMax),
    priceMin: useDebouncedValue(filters.priceMin),
    priceMax: useDebouncedValue(filters.priceMax),
  };

  const dealersQ = useDealerList();

  const queryParams = useMemo(
    () => ({
      q: debounced.search || undefined,
      make: debounced.make || undefined,
      model: debounced.model || undefined,
      year_min: parseOptionalNumber(debounced.yearMin),
      year_max: parseOptionalNumber(debounced.yearMax),
      price_min: parseOptionalNumber(debounced.priceMin),
      price_max: parseOptionalNumber(debounced.priceMax),
      // 'all' is a UI-only value meaning "don't filter"; the API takes either a
      // real status or nothing at all.
      status: filters.status && filters.status !== 'all' ? filters.status : undefined,
      dealer_id: filters.dealerId ? Number(filters.dealerId) : undefined,
      page: page + 1,
      page_size: pageSize,
      sort: SERVER_SORTABLE.has(sort.key) ? sort.key : 'id',
      order: sort.dir,
    }),
    [
      debounced.search,
      debounced.make,
      debounced.model,
      debounced.yearMin,
      debounced.yearMax,
      debounced.priceMin,
      debounced.priceMax,
      filters.status,
      filters.dealerId,
      page,
      pageSize,
      sort,
    ],
  );

  const vehiclesQ = useQuery({
    queryKey: ['vehicles', queryParams],
    queryFn: () => listVehicles(queryParams),
    placeholderData: (prev) => prev,
  });

  // Any filter change invalidates the current page offset.
  useEffect(() => {
    setPage(0);
  }, [
    debounced.search,
    debounced.make,
    debounced.model,
    debounced.yearMin,
    debounced.yearMax,
    debounced.priceMin,
    debounced.priceMax,
    filters.status,
    filters.dealerId,
  ]);

  useEffect(() => {
    if (vehiclesQ.isError) enqueueSnackbar(getErrorMessage(vehiclesQ.error), { variant: 'error' });
  }, [vehiclesQ.isError, vehiclesQ.error, enqueueSnackbar]);

  useEffect(() => {
    localStorage.setItem(SAVED_FILTERS_KEY, JSON.stringify(savedFilters));
  }, [savedFilters]);

  const dealerName = useMemo(() => {
    const map = new Map((dealersQ.data ?? []).map((d) => [d.id, d.name]));
    return (id: number) => map.get(id) ?? `Dealer #${id}`;
  }, [dealersQ.data]);

  const rows = useMemo(() => vehiclesQ.data?.items ?? [], [vehiclesQ.data]);
  const total = vehiclesQ.data?.meta.total ?? 0;
  const activeFilterCount = Object.entries(filters).filter(([, v]) => v !== '').length;

  const selectedVehicles = useMemo(() => rows.filter((v) => selected.has(v.id)), [rows, selected]);

  function exportRows(source: readonly Vehicle[], prefix: string) {
    if (source.length === 0) {
      enqueueSnackbar('Nothing to export', { variant: 'info' });
      return;
    }
    downloadCsv(stampedFilename(prefix), source, [
      { header: 'ID', value: (v) => v.id },
      { header: 'Dealer', value: (v) => dealerName(v.dealer_id) },
      { header: 'Year', value: (v) => v.year },
      { header: 'Make', value: (v) => v.make },
      { header: 'Model', value: (v) => v.model },
      { header: 'Trim', value: (v) => v.trim },
      { header: 'VIN', value: (v) => v.vin },
      { header: 'Price', value: (v) => v.price },
      { header: 'Currency', value: (v) => v.currency },
      { header: 'Mileage', value: (v) => v.mileage },
      { header: 'Transmission', value: (v) => v.transmission },
      { header: 'Fuel', value: (v) => v.fuel_type },
      { header: 'Drivetrain', value: (v) => v.drivetrain },
      { header: 'Body type', value: (v) => v.body_type },
      { header: 'Status', value: (v) => v.status },
      { header: 'Discount from', value: (v) => v.original_price },
      { header: 'Created', value: (v) => v.first_seen_at },
      { header: 'Last checked', value: (v) => v.last_seen_at },
      { header: 'Last updated', value: (v) => v.updated_at },
      { header: 'URL', value: (v) => v.url },
    ]);
    enqueueSnackbar(`Exported ${source.length} vehicle${source.length === 1 ? '' : 's'}`, { variant: 'success' });
  }

  const columns: Column<Vehicle>[] = useMemo(
    () => [
      {
        key: 'vehicle',
        header: 'Vehicle',
        minWidth: 260,
        hideable: false,
        sortable: false,
        render: (v) => (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, minWidth: 0 }}>
            <VehicleThumb />
            <Box sx={{ minWidth: 0 }}>
              <Typography variant="body2" noWrap sx={{ fontWeight: 560 }}>
                {vehicleTitle(v)}
              </Typography>
              <Typography variant="caption" color="text.secondary" noWrap sx={{ display: 'block' }}>
                {v.vin ?? `Stock ${v.external_id}`}
              </Typography>
            </Box>
          </Box>
        ),
      },
      { key: 'year', header: 'Year', width: 80, align: 'right', value: (v) => v.year },
      { key: 'make', header: 'Make', width: 110, sortable: false, value: (v) => v.make },
      { key: 'model', header: 'Model', width: 130, sortable: false, value: (v) => v.model },
      {
        key: 'price',
        header: 'Price',
        width: 120,
        align: 'right',
        value: (v) => (v.price ? Number(v.price) : null),
        render: (v) => (
          <Box>
            <Typography variant="body2" sx={{ fontWeight: 620 }}>
              {formatMoney(v.price, v.currency)}
            </Typography>
            {v.original_price && (
              // Struck-through pre-discount figure — the visual cue that this
              // listing is on sale, which a bare price column cannot convey.
              <Typography
                variant="caption"
                color="text.disabled"
                sx={{ textDecoration: 'line-through' }}
              >
                {formatMoney(v.original_price, v.currency)}
              </Typography>
            )}
          </Box>
        ),
      },
      {
        key: 'mileage',
        header: 'Mileage',
        width: 110,
        align: 'right',
        value: (v) => v.mileage,
        render: (v) => (
          <Typography variant="body2" color={v.mileage == null ? 'text.disabled' : 'text.primary'}>
            {v.mileage == null ? '—' : `${v.mileage.toLocaleString()} km`}
          </Typography>
        ),
      },
      {
        key: 'vin',
        header: 'VIN',
        width: 170,
        sortable: false,
        defaultHidden: true,
        value: (v) => v.vin,
        render: (v) => (
          <Typography
            variant="caption"
            sx={{ fontFamily: 'ui-monospace, monospace', color: v.vin ? 'text.secondary' : 'text.disabled' }}
          >
            {v.vin ?? '—'}
          </Typography>
        ),
      },
      {
        key: 'transmission',
        header: 'Transmission',
        width: 130,
        sortable: false,
        defaultHidden: true,
        value: (v) => v.transmission,
        render: (v) => <MutedText value={v.transmission} />,
      },
      {
        key: 'fuel_type',
        header: 'Fuel',
        width: 110,
        sortable: false,
        defaultHidden: true,
        value: (v) => v.fuel_type,
        render: (v) => <MutedText value={v.fuel_type} />,
      },
      {
        key: 'dealer_id',
        header: 'Dealer',
        width: 160,
        sortable: false,
        value: (v) => dealerName(v.dealer_id),
        render: (v) => (
          <Typography variant="body2" noWrap>
            {dealerName(v.dealer_id)}
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
        // Renamed from "Updated": this moves on every sync, so it reads "today"
        // for every active car and says nothing about the listing changing.
        header: 'Last checked',
        width: 140,
        value: (v) => v.last_seen_at,
        render: (v) => (
          <Tooltip title={`Last confirmed still listed: ${formatDate(v.last_seen_at)}`}>
            <Typography variant="body2" color="text.secondary">
              {formatRelative(v.last_seen_at)}
            </Typography>
          </Tooltip>
        ),
      },
      {
        key: 'updated_at',
        header: 'Last updated',
        width: 140,
        sortable: false,
        value: (v) => v.updated_at,
        render: (v) => (
          <Tooltip
            title={
              v.updated_at
                ? `Listing last changed: ${formatDate(v.updated_at)}`
                : 'No changes recorded yet'
            }
          >
            <Typography
              variant="body2"
              color={v.updated_at ? 'text.secondary' : 'text.disabled'}
            >
              {v.updated_at ? formatRelative(v.updated_at) : '—'}
            </Typography>
          </Tooltip>
        ),
      },
    ],
    [dealerName],
  );

  return (
    <Box>
      <PageHeader
        title="Inventory"
        description="Every vehicle the platform has scraped, across all dealerships. Filter, sort and export the exact slice you need."
        eyebrow={vehiclesQ.data ? `${total.toLocaleString()} vehicles` : undefined}
        actions={
          <>
            <Button
              variant="outlined"
              startIcon={<FileDownloadRoundedIcon />}
              onClick={() => exportRows(rows, 'inventory-page')}
              disabled={rows.length === 0}
            >
              Export page
            </Button>
            <Button
              variant="outlined"
              startIcon={savedFilters.length ? <BookmarkAddedRoundedIcon /> : <BookmarkBorderRoundedIcon />}
              onClick={(e) => setSavedAnchor(e.currentTarget)}
            >
              Saved filters{savedFilters.length ? ` (${savedFilters.length})` : ''}
            </Button>
          </>
        }
      />

      <FilterBar activeCount={activeFilterCount} onReset={() => setFilters(EMPTY_FILTERS)}>
        <SearchField
          value={filters.search}
          onChange={(v) => set('search', v)}
          placeholder="Make, model, trim or VIN"
          width={240}
          aria-label="Search inventory"
        />
        <FilterSelect
          label="Status"
          value={filters.status}
          onChange={(v) => set('status', v)}
          width={128}
          options={[
            { value: 'active', label: 'Active' },
            { value: 'removed', label: 'Removed' },
            { value: 'sold', label: 'Sold' },
            // Explicit, because the page no longer defaults to everything and
            // "clear the filter" is not discoverable as a way to see history.
            { value: 'all', label: 'All statuses' },
          ]}
        />
        <FilterSelect
          label="Dealer"
          value={filters.dealerId}
          onChange={(v) => set('dealerId', v)}
          width={180}
          options={(dealersQ.data ?? []).map((d) => ({ value: String(d.id), label: d.name }))}
        />
        <TextField
          size="small"
          label="Make"
          value={filters.make}
          onChange={(e) => set('make', e.target.value)}
          sx={{ width: 120 }}
        />
        <TextField
          size="small"
          label="Model"
          value={filters.model}
          onChange={(e) => set('model', e.target.value)}
          sx={{ width: 120 }}
        />
        <NumberRange
          label="Year"
          min={filters.yearMin}
          max={filters.yearMax}
          onMinChange={(v) => set('yearMin', v)}
          onMaxChange={(v) => set('yearMax', v)}
          width={82}
        />
        <NumberRange
          label="Price"
          min={filters.priceMin}
          max={filters.priceMax}
          onMinChange={(v) => set('priceMin', v)}
          onMaxChange={(v) => set('priceMax', v)}
          width={96}
        />
      </FilterBar>

      {activeFilterCount > 0 && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2, flexWrap: 'wrap' }}>
          <Typography variant="caption" color="text.secondary">
            Filtering by
          </Typography>
          {(Object.entries(filters) as [keyof FilterState, string][])
            .filter(([, value]) => value !== '')
            .map(([key, value]) => (
              <Chip
                key={key}
                size="small"
                variant="outlined"
                label={`${labelForFilter(key)}: ${key === 'dealerId' ? dealerName(Number(value)) : value}`}
                onDelete={() => set(key, '')}
                sx={{ bgcolor: alpha(theme.palette.primary.main, 0.06), borderColor: 'transparent' }}
              />
            ))}
        </Box>
      )}

      {vehiclesQ.isError && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {getErrorMessage(vehiclesQ.error)}
        </Alert>
      )}

      <SectionCard flush>
        <DataTable
          rows={rows}
          columns={columns}
          getRowId={(v) => v.id}
          loading={vehiclesQ.isLoading}
          skeletonRows={10}
          onRowClick={(v) => navigate(`/vehicles/${v.id}`)}
          sort={sort}
          onSortChange={setSort}
          page={page}
          pageSize={pageSize}
          rowCount={total}
          onPageChange={setPage}
          onPageSizeChange={(size) => {
            setPageSize(size);
            setPage(0);
          }}
          pageSizeOptions={[25, 50, 100, 200]}
          selectable
          selected={selected}
          onSelectedChange={setSelected}
          storageKey="vehicles"
          rowNoun="vehicles"
          maxHeight="max(520px, calc(100vh - 240px))"
          bulkActions={() => (
            <>
              <Button
                size="small"
                startIcon={<FileDownloadRoundedIcon sx={{ fontSize: 16 }} />}
                onClick={() => exportRows(selectedVehicles, 'inventory-selection')}
                sx={{ color: '#fff', '&:hover': { bgcolor: alpha('#fff', 0.12) } }}
              >
                Export CSV
              </Button>
              <Button
                size="small"
                startIcon={<ContentCopyRoundedIcon sx={{ fontSize: 16 }} />}
                onClick={() => {
                  const vins = selectedVehicles.map((v) => v.vin).filter(Boolean).join('\n');
                  if (!vins) {
                    enqueueSnackbar('None of the selected vehicles have a VIN', { variant: 'warning' });
                    return;
                  }
                  navigator.clipboard
                    ?.writeText(vins)
                    .then(() => enqueueSnackbar('VINs copied to clipboard', { variant: 'success' }))
                    .catch(() => enqueueSnackbar('Could not copy to clipboard', { variant: 'error' }));
                }}
                sx={{ color: '#fff', '&:hover': { bgcolor: alpha('#fff', 0.12) } }}
              >
                Copy VINs
              </Button>
            </>
          )}
          empty={
            activeFilterCount > 0 ? (
              <EmptyState
                art="filter"
                title="No vehicles match these filters"
                description="Widen the year or price range, or clear the filters to browse the full inventory."
                action={
                  <Button variant="outlined" onClick={() => setFilters(EMPTY_FILTERS)}>
                    Clear filters
                  </Button>
                }
              />
            ) : (
              <EmptyState
                art="vehicles"
                title="No vehicles yet"
                description="Inventory appears here after a dealership completes its first scrape."
                action={
                  <Button variant="contained" onClick={() => navigate('/dealers')}>
                    Go to dealerships
                  </Button>
                }
              />
            )
          }
        />
      </SectionCard>

      {/* ── Saved filters ──────────────────────────────────────────────── */}
      <Menu
        anchorEl={savedAnchor}
        open={Boolean(savedAnchor)}
        onClose={() => setSavedAnchor(null)}
        slotProps={{ paper: { sx: { minWidth: 280 } } }}
      >
        <Box sx={{ px: 1.25, py: 1 }}>
          <Typography variant="overline" color="text.secondary">
            Save current filters
          </Typography>
          <Box sx={{ display: 'flex', gap: 1, mt: 0.75 }}>
            <TextField
              size="small"
              fullWidth
              placeholder="e.g. Under $20k SUVs"
              value={newFilterName}
              onChange={(e) => setNewFilterName(e.target.value)}
              onKeyDown={(e) => e.stopPropagation()}
            />
            <Button
              size="small"
              variant="contained"
              disabled={!newFilterName.trim() || activeFilterCount === 0}
              onClick={() => {
                setSavedFilters((prev) => [
                  ...prev.filter((f) => f.name !== newFilterName.trim()),
                  { name: newFilterName.trim(), filters },
                ]);
                setNewFilterName('');
                enqueueSnackbar('Filter saved', { variant: 'success' });
              }}
            >
              Save
            </Button>
          </Box>
          {activeFilterCount === 0 && (
            <Typography variant="caption" color="text.disabled" sx={{ display: 'block', mt: 0.75 }}>
              Apply at least one filter first.
            </Typography>
          )}
        </Box>

        {savedFilters.length > 0 && <Divider sx={{ my: 0.5 }} />}

        {savedFilters.map((saved) => (
          <MenuItem
            key={saved.name}
            onClick={() => {
              setFilters(saved.filters);
              setSavedAnchor(null);
            }}
          >
            <ListItemIcon>
              <BookmarkBorderRoundedIcon fontSize="small" />
            </ListItemIcon>
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography variant="body2" noWrap>
                {saved.name}
              </Typography>
            </Box>
            <IconButton
              size="small"
              aria-label={`Delete saved filter ${saved.name}`}
              onClick={(e) => {
                e.stopPropagation();
                setSavedFilters((prev) => prev.filter((f) => f.name !== saved.name));
              }}
            >
              <DeleteOutlineRoundedIcon sx={{ fontSize: 15 }} />
            </IconButton>
          </MenuItem>
        ))}
      </Menu>
    </Box>
  );
}

function labelForFilter(key: keyof FilterState): string {
  const labels: Record<keyof FilterState, string> = {
    search: 'Search',
    make: 'Make',
    model: 'Model',
    yearMin: 'Year from',
    yearMax: 'Year to',
    priceMin: 'Price from',
    priceMax: 'Price to',
    status: 'Status',
    dealerId: 'Dealer',
  };
  return labels[key];
}

function MutedText({ value }: { value: string | null }) {
  return (
    <Typography variant="body2" color={value ? 'text.primary' : 'text.disabled'} noWrap>
      {value ?? '—'}
    </Typography>
  );
}

/** Vehicle placeholder thumbnail (the scraper does not capture photos). */
function VehicleThumb() {
  const tk = useTokens();

  return (
    <Box
      sx={{
        width: 52,
        height: 38,
        flexShrink: 0,
        borderRadius: `${tk.radii.sm}px`,
        bgcolor: tk.surfaces.sunken,
        border: `1px solid ${tk.surfaces.border}`,
        display: 'grid',
        placeItems: 'center',
        color: 'text.disabled',
      }}
    >
      <DirectionsCarFilledRoundedIcon sx={{ fontSize: 17 }} />
    </Box>
  );
}
