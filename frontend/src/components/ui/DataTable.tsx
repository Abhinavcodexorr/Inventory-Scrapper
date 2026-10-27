import { useEffect, useMemo, useState, type ReactNode } from 'react';
import {
  Box,
  Checkbox,
  Divider,
  IconButton,
  ListItemText,
  Menu,
  MenuItem,
  Select,
  Skeleton,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from '@mui/material';
import { alpha, useTheme } from '@mui/material/styles';
import ArrowUpwardRoundedIcon from '@mui/icons-material/ArrowUpwardRounded';
import ArrowDownwardRoundedIcon from '@mui/icons-material/ArrowDownwardRounded';
import UnfoldMoreRoundedIcon from '@mui/icons-material/UnfoldMoreRounded';
import ViewWeekRoundedIcon from '@mui/icons-material/ViewWeekRounded';
import ChevronLeftRoundedIcon from '@mui/icons-material/ChevronLeftRounded';
import ChevronRightRoundedIcon from '@mui/icons-material/ChevronRightRounded';
import { useTokens } from '../../theme/useTokens';
import { useAppSettings } from '../../theme/settings';

export interface Column<T> {
  key: string;
  header: ReactNode;
  /** Custom cell body. Falls back to `value()`. */
  render?: (row: T) => ReactNode;
  /** Sortable/exportable scalar for the cell. */
  value?: (row: T) => string | number | null | undefined;
  width?: number | string;
  minWidth?: number;
  align?: 'left' | 'right' | 'center';
  sortable?: boolean;
  /** Allow the user to hide this column. Defaults to true. */
  hideable?: boolean;
  defaultHidden?: boolean;
  /** Tooltip on the header cell. */
  hint?: string;
}

export interface SortState {
  key: string;
  dir: 'asc' | 'desc';
}

interface Props<T> {
  rows: readonly T[];
  columns: Column<T>[];
  getRowId: (row: T) => string | number;

  loading?: boolean;
  skeletonRows?: number;
  /** Shown when there are no rows and we are not loading. */
  empty?: ReactNode;

  onRowClick?: (row: T) => void;
  /** Highlights a row as the current context (e.g. open detail). */
  isRowActive?: (row: T) => boolean;

  /** Controlled sort. Omit `onSortChange` to sort client-side. */
  sort?: SortState | null;
  onSortChange?: (sort: SortState) => void;

  /** Zero-based page index. Omit pagination props entirely to render all rows. */
  page?: number;
  pageSize?: number;
  /** Server-side total. Omit for client-side pagination of `rows`. */
  rowCount?: number;
  onPageChange?: (page: number) => void;
  onPageSizeChange?: (size: number) => void;
  pageSizeOptions?: number[];

  selectable?: boolean;
  selected?: ReadonlySet<string | number>;
  onSelectedChange?: (next: Set<string | number>) => void;
  /** Rendered in the floating bar while rows are selected. */
  bulkActions?: (ids: (string | number)[]) => ReactNode;

  /** Persist hidden-column choices under this key. */
  storageKey?: string;
  /** Cap the scroll area; the header stays pinned. */
  maxHeight?: number | string;
  /** Row label used in the footer, e.g. "vehicles". */
  rowNoun?: string;
}

const HIDDEN_PREFIX = 'inventory-hub.columns.';

/**
 * The single table implementation used across the app.
 *
 * Handles sticky headers, client or server sorting/pagination, bulk selection,
 * column visibility and skeleton loading — so every list in the product has the
 * same behaviour and the same visual rhythm.
 */
export function DataTable<T>({
  rows,
  columns,
  getRowId,
  loading = false,
  skeletonRows = 8,
  empty,
  onRowClick,
  isRowActive,
  sort,
  onSortChange,
  page,
  pageSize,
  rowCount,
  onPageChange,
  onPageSizeChange,
  pageSizeOptions = [25, 50, 100],
  selectable = false,
  selected,
  onSelectedChange,
  bulkActions,
  storageKey,
  maxHeight,
  rowNoun = 'rows',
}: Props<T>) {
  const tk = useTokens();
  const theme = useTheme();
  const { settings } = useAppSettings();
  const compact = settings.density === 'compact';

  const [hidden, setHidden] = useState<Set<string>>(() => {
    const fromDefaults = new Set(columns.filter((c) => c.defaultHidden).map((c) => c.key));
    if (!storageKey || typeof window === 'undefined') return fromDefaults;
    try {
      const raw = window.localStorage.getItem(HIDDEN_PREFIX + storageKey);
      if (!raw) return fromDefaults;
      return new Set(JSON.parse(raw) as string[]);
    } catch {
      return fromDefaults;
    }
  });
  const [colMenuAnchor, setColMenuAnchor] = useState<HTMLElement | null>(null);

  useEffect(() => {
    if (!storageKey) return;
    try {
      window.localStorage.setItem(HIDDEN_PREFIX + storageKey, JSON.stringify([...hidden]));
    } catch {
      /* non-fatal */
    }
  }, [hidden, storageKey]);

  const [clientSort, setClientSort] = useState<SortState | null>(null);
  const activeSort = onSortChange ? (sort ?? null) : (sort ?? clientSort);

  const visibleColumns = useMemo(() => columns.filter((c) => !hidden.has(c.key)), [columns, hidden]);
  const hideableColumns = useMemo(() => columns.filter((c) => c.hideable !== false), [columns]);

  // Client-side sort only applies when the caller has not taken control of it.
  const sortedRows = useMemo(() => {
    if (onSortChange || !activeSort) return rows;
    const col = columns.find((c) => c.key === activeSort.key);
    if (!col?.value) return rows;
    const dir = activeSort.dir === 'asc' ? 1 : -1;
    return [...rows].sort((a, b) => {
      const av = col.value!(a);
      const bv = col.value!(b);
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * dir;
      return String(av).localeCompare(String(bv), undefined, { numeric: true }) * dir;
    });
  }, [rows, columns, activeSort, onSortChange]);

  const paginated = useMemo(() => {
    // `rowCount` present means the server already paginated for us.
    if (rowCount !== undefined || page === undefined || pageSize === undefined) return sortedRows;
    const start = page * pageSize;
    return sortedRows.slice(start, start + pageSize);
  }, [sortedRows, rowCount, page, pageSize]);

  const total = rowCount ?? sortedRows.length;
  const hasPagination = page !== undefined && pageSize !== undefined;
  const pageCount = hasPagination ? Math.max(1, Math.ceil(total / pageSize)) : 1;

  const selectedIds = useMemo(() => [...(selected ?? new Set())], [selected]);
  const pageIds = useMemo(() => paginated.map(getRowId), [paginated, getRowId]);
  const allOnPageSelected = pageIds.length > 0 && pageIds.every((id) => selected?.has(id));
  const someOnPageSelected = pageIds.some((id) => selected?.has(id)) && !allOnPageSelected;

  function toggleAllOnPage() {
    if (!onSelectedChange) return;
    const next = new Set(selected ?? []);
    if (allOnPageSelected) pageIds.forEach((id) => next.delete(id));
    else pageIds.forEach((id) => next.add(id));
    onSelectedChange(next);
  }

  function toggleRow(id: string | number) {
    if (!onSelectedChange) return;
    const next = new Set(selected ?? []);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    onSelectedChange(next);
  }

  function requestSort(col: Column<T>) {
    if (col.sortable === false) return;
    const nextDir: 'asc' | 'desc' = activeSort?.key === col.key && activeSort.dir === 'desc' ? 'asc' : 'desc';
    const next: SortState = { key: col.key, dir: nextDir };
    if (onSortChange) onSortChange(next);
    else setClientSort(next);
  }

  const cellPadY = compact ? 1 : 1.5;
  const rowHeight = compact ? 44 : 56;

  const headCellSx = {
    position: 'sticky' as const,
    top: 0,
    zIndex: 2,
    bgcolor: tk.surfaces.sunken,
    borderBottom: `1px solid ${tk.surfaces.border}`,
    py: 1.25,
    fontSize: '0.75rem',
    fontWeight: 620,
    color: 'text.secondary',
    whiteSpace: 'nowrap' as const,
  };

  return (
    <Box sx={{ position: 'relative', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
      {/* Floating bulk-action bar — only mounts while something is selected. */}
      {selectable && selectedIds.length > 0 && (
        <Box
          sx={{
            position: 'sticky',
            top: 8,
            zIndex: 5,
            mx: 2,
            mb: -5,
            display: 'flex',
            alignItems: 'center',
            gap: 1.5,
            px: 2,
            py: 1.25,
            borderRadius: `${tk.radii.control}px`,
            bgcolor: theme.palette.mode === 'light' ? '#0F172A' : tk.surfaces.raised,
            color: '#fff',
            boxShadow: tk.elevation.lg,
            '@keyframes bulkbar-in': {
              from: { opacity: 0, transform: 'translate3d(0,-6px,0)' },
              to: { opacity: 1, transform: 'none' },
            },
            animation: `bulkbar-in 200ms ${tk.motion.entrance}`,
          }}
        >
          <Typography variant="body2" sx={{ fontWeight: 600 }}>
            {selectedIds.length} selected
          </Typography>
          <Box sx={{ flex: 1 }} />
          {bulkActions?.(selectedIds)}
          <Divider orientation="vertical" flexItem sx={{ borderColor: alpha('#fff', 0.2) }} />
          <IconButton
            size="small"
            onClick={() => onSelectedChange?.(new Set())}
            sx={{ color: alpha('#fff', 0.8), '&:hover': { color: '#fff', bgcolor: alpha('#fff', 0.12) } }}
            aria-label="Clear selection"
          >
            <Typography variant="caption" sx={{ fontWeight: 600 }}>
              Clear
            </Typography>
          </IconButton>
        </Box>
      )}

      <Box
        sx={{
          overflow: 'auto',
          maxHeight,
          flex: 1,
          minHeight: 0,
          // Fade the sticky header edge when content scrolls under it.
          '& table': { borderCollapse: 'separate', borderSpacing: 0 },
        }}
      >
        <Table stickyHeader size={compact ? 'small' : 'medium'} sx={{ minWidth: 640 }}>
          <TableHead>
            <TableRow>
              {selectable && (
                <TableCell padding="checkbox" sx={{ ...headCellSx, width: 48, pl: 1.5 }}>
                  <Checkbox
                    size="small"
                    checked={allOnPageSelected}
                    indeterminate={someOnPageSelected}
                    onChange={toggleAllOnPage}
                    slotProps={{ input: { 'aria-label': 'Select all rows on this page' } }}
                  />
                </TableCell>
              )}

              {visibleColumns.map((col) => {
                const isSorted = activeSort?.key === col.key;
                const sortable = col.sortable !== false && (Boolean(col.value) || Boolean(onSortChange));
                return (
                  <TableCell
                    key={col.key}
                    align={col.align ?? 'left'}
                    sx={{
                      ...headCellSx,
                      width: col.width,
                      minWidth: col.minWidth,
                      cursor: sortable ? 'pointer' : 'default',
                      userSelect: 'none',
                      transition: `color ${tk.motion.fast} ${tk.motion.ease}`,
                      '&:hover': sortable ? { color: 'text.primary' } : undefined,
                      ...(isSorted ? { color: 'text.primary' } : null),
                    }}
                    onClick={sortable ? () => requestSort(col) : undefined}
                    aria-sort={isSorted ? (activeSort.dir === 'asc' ? 'ascending' : 'descending') : undefined}
                  >
                    <Tooltip title={col.hint ?? ''} disableHoverListener={!col.hint}>
                      <Box
                        component="span"
                        sx={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: 0.5,
                          justifyContent: col.align === 'right' ? 'flex-end' : 'flex-start',
                          width: '100%',
                        }}
                      >
                        {col.header}
                        {sortable && (
                          <Box
                            component="span"
                            sx={{
                              display: 'inline-flex',
                              opacity: isSorted ? 1 : 0.35,
                              transition: `opacity ${tk.motion.fast} ${tk.motion.ease}`,
                              '& svg': { fontSize: 14 },
                            }}
                          >
                            {!isSorted ? (
                              <UnfoldMoreRoundedIcon />
                            ) : activeSort.dir === 'asc' ? (
                              <ArrowUpwardRoundedIcon />
                            ) : (
                              <ArrowDownwardRoundedIcon />
                            )}
                          </Box>
                        )}
                      </Box>
                    </Tooltip>
                  </TableCell>
                );
              })}

              {hideableColumns.length > 0 && (
                <TableCell align="right" sx={{ ...headCellSx, width: 48, pr: 1 }}>
                  <Tooltip title="Columns">
                    <IconButton size="small" onClick={(e) => setColMenuAnchor(e.currentTarget)} aria-label="Toggle columns">
                      <ViewWeekRoundedIcon sx={{ fontSize: 17 }} />
                    </IconButton>
                  </Tooltip>
                </TableCell>
              )}
            </TableRow>
          </TableHead>

          <TableBody>
            {loading &&
              Array.from({ length: skeletonRows }).map((_, i) => (
                <TableRow key={`sk-${i}`} sx={{ height: rowHeight }}>
                  {selectable && (
                    <TableCell padding="checkbox" sx={{ borderBottomColor: tk.surfaces.border }}>
                      <Skeleton variant="rounded" width={18} height={18} />
                    </TableCell>
                  )}
                  {visibleColumns.map((col) => (
                    <TableCell key={col.key} sx={{ py: cellPadY, borderBottomColor: tk.surfaces.border }}>
                      <Skeleton width={`${45 + ((i * 13 + col.key.length * 7) % 45)}%`} height={16} />
                    </TableCell>
                  ))}
                  {hideableColumns.length > 0 && <TableCell sx={{ borderBottomColor: tk.surfaces.border }} />}
                </TableRow>
              ))}

            {!loading && paginated.length === 0 && (
              <TableRow>
                <TableCell
                  colSpan={visibleColumns.length + (selectable ? 1 : 0) + (hideableColumns.length > 0 ? 1 : 0)}
                  sx={{ borderBottom: 'none', p: 0 }}
                >
                  {empty}
                </TableCell>
              </TableRow>
            )}

            {!loading &&
              paginated.map((row, rowIndex) => {
                const id = getRowId(row);
                const isSelected = Boolean(selected?.has(id));
                const isActive = isRowActive?.(row) ?? false;
                return (
                  <TableRow
                    key={id}
                    hover={false}
                    tabIndex={onRowClick ? 0 : -1}
                    onClick={onRowClick ? () => onRowClick(row) : undefined}
                    onKeyDown={
                      onRowClick
                        ? (e) => {
                            if (e.key === 'Enter' || e.key === ' ') {
                              e.preventDefault();
                              onRowClick(row);
                            }
                          }
                        : undefined
                    }
                    sx={{
                      height: rowHeight,
                      cursor: onRowClick ? 'pointer' : 'default',
                      transition: `background-color ${tk.motion.fast} ${tk.motion.ease}`,
                      bgcolor: isSelected ? tk.surfaces.selected : isActive ? tk.surfaces.hover : 'transparent',
                      '& td': {
                        borderBottomColor: tk.surfaces.border,
                        py: cellPadY,
                        transition: `background-color ${tk.motion.fast} ${tk.motion.ease}`,
                      },
                      '&:hover td': { backgroundColor: isSelected ? 'transparent' : tk.surfaces.hover },
                      // Rounded hover pill: only the outer cells round off.
                      '&:hover td:first-of-type': { borderTopLeftRadius: tk.radii.sm, borderBottomLeftRadius: tk.radii.sm },
                      '&:hover td:last-of-type': { borderTopRightRadius: tk.radii.sm, borderBottomRightRadius: tk.radii.sm },
                      '&:last-of-type td': { borderBottom: 'none' },
                      '&:focus-visible': { outline: 'none', boxShadow: `inset ${tk.focusRing}` },
                      // Stagger only the first screenful; later pages appear instantly.
                      ...(rowIndex < 12
                        ? {
                            '@keyframes row-in': { from: { opacity: 0 }, to: { opacity: 1 } },
                            animation: `row-in 260ms ${tk.motion.entrance} both`,
                            animationDelay: `${rowIndex * 18}ms`,
                            '@media (prefers-reduced-motion: reduce)': { animation: 'none' },
                          }
                        : null),
                    }}
                  >
                    {selectable && (
                      <TableCell padding="checkbox" sx={{ pl: 1.5 }} onClick={(e) => e.stopPropagation()}>
                        <Checkbox
                          size="small"
                          checked={isSelected}
                          onChange={() => toggleRow(id)}
                          slotProps={{ input: { 'aria-label': `Select row ${id}` } }}
                        />
                      </TableCell>
                    )}
                    {visibleColumns.map((col) => (
                      <TableCell key={col.key} align={col.align ?? 'left'} sx={{ width: col.width, minWidth: col.minWidth }}>
                        {col.render ? col.render(row) : (col.value?.(row) ?? '—')}
                      </TableCell>
                    ))}
                    {hideableColumns.length > 0 && <TableCell />}
                  </TableRow>
                );
              })}
          </TableBody>
        </Table>
      </Box>

      {hasPagination && (
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 2,
            px: 2,
            py: 1.5,
            borderTop: `1px solid ${tk.surfaces.border}`,
            flexWrap: 'wrap',
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Typography variant="caption" color="text.secondary">
              Rows per page
            </Typography>
            <Select
              size="small"
              value={pageSize}
              onChange={(e) => onPageSizeChange?.(Number(e.target.value))}
              disabled={!onPageSizeChange}
              sx={{ '& .MuiSelect-select': { py: 0.5, fontSize: '0.8125rem' }, minWidth: 72 }}
            >
              {pageSizeOptions.map((n) => (
                <MenuItem key={n} value={n}>
                  {n}
                </MenuItem>
              ))}
            </Select>
          </Box>

          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Typography variant="caption" color="text.secondary" sx={{ mr: 1 }}>
              {total === 0
                ? `No ${rowNoun}`
                : `${(page * pageSize + 1).toLocaleString()}–${Math.min((page + 1) * pageSize, total).toLocaleString()} of ${total.toLocaleString()}`}
            </Typography>
            <IconButton
              size="small"
              disabled={page <= 0}
              onClick={() => onPageChange?.(page - 1)}
              aria-label="Previous page"
            >
              <ChevronLeftRoundedIcon fontSize="small" />
            </IconButton>
            <IconButton
              size="small"
              disabled={page >= pageCount - 1}
              onClick={() => onPageChange?.(page + 1)}
              aria-label="Next page"
            >
              <ChevronRightRoundedIcon fontSize="small" />
            </IconButton>
          </Box>
        </Box>
      )}

      <Menu anchorEl={colMenuAnchor} open={Boolean(colMenuAnchor)} onClose={() => setColMenuAnchor(null)}>
        <Typography variant="overline" sx={{ px: 1.25, py: 0.5, display: 'block', color: 'text.secondary' }}>
          Visible columns
        </Typography>
        {hideableColumns.map((col) => (
          <MenuItem
            key={col.key}
            onClick={() =>
              setHidden((prev) => {
                const next = new Set(prev);
                if (next.has(col.key)) next.delete(col.key);
                else next.add(col.key);
                return next;
              })
            }
          >
            <Checkbox size="small" checked={!hidden.has(col.key)} sx={{ p: 0.5, mr: 1 }} />
            <ListItemText slotProps={{ primary: { sx: { fontSize: '0.875rem' } } }}>{col.header}</ListItemText>
          </MenuItem>
        ))}
      </Menu>
    </Box>
  );
}
