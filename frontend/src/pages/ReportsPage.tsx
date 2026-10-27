import { useState, type ReactNode } from 'react';
import { useSnackbar } from 'notistack';
import { Box, Button, CircularProgress, Typography } from '@mui/material';
import { alpha, useTheme } from '@mui/material/styles';
import StorefrontRoundedIcon from '@mui/icons-material/StorefrontRounded';
import DirectionsCarFilledRoundedIcon from '@mui/icons-material/DirectionsCarFilledRounded';
import BoltRoundedIcon from '@mui/icons-material/BoltRounded';
import FileDownloadRoundedIcon from '@mui/icons-material/FileDownloadRounded';
import { listDealers } from '../api/dealers';
import { listVehicles } from '../api/vehicles';
import { getErrorMessage } from '../api/client';
import { AppearIn, PageHeader, SectionCard, SoftIconTile, Surface } from '../components/ui';
import { useAllRuns, useDealerList } from '../hooks/useDealers';
import { downloadCsv, stampedFilename } from '../utils/csv';
import { formatDate } from '../utils/format';

interface ReportDef {
  id: string;
  title: string;
  description: string;
  icon: ReactNode;
  color: string;
  run: () => Promise<void>;
}

const PAGE_SIZE = 500;

/**
 * Every export here re-fetches fresh data at click time rather than reusing
 * cached pages — a report should reflect the platform right now, not whatever
 * a table happened to have paginated into memory.
 */
export function ReportsPage() {
  const theme = useTheme();
  const { enqueueSnackbar } = useSnackbar();
  const [runningId, setRunningId] = useState<string | null>(null);

  const dealersQ = useDealerList();
  const runsAgg = useAllRuns(dealersQ.data, 30, Boolean(dealersQ.data?.length));

  async function withRunning(id: string, fn: () => Promise<void>) {
    setRunningId(id);
    try {
      await fn();
    } catch (e) {
      enqueueSnackbar(getErrorMessage(e), { variant: 'error' });
    } finally {
      setRunningId(null);
    }
  }

  async function exportDealers() {
    const dealers = await listDealers();
    if (dealers.length === 0) {
      enqueueSnackbar('No dealerships to export', { variant: 'info' });
      return;
    }
    downloadCsv(stampedFilename('dealerships'), dealers, [
      { header: 'ID', value: (d) => d.id },
      { header: 'Name', value: (d) => d.name },
      { header: 'Website', value: (d) => d.base_url },
      { header: 'Provider', value: (d) => d.website_provider },
      { header: 'City', value: (d) => d.city },
      { header: 'Province', value: (d) => d.province },
      { header: 'Phone', value: (d) => d.phone },
      { header: 'Active', value: (d) => (d.active ? 'yes' : 'no') },
      { header: 'Inventory count', value: (d) => d.inventory_count },
      { header: 'Last synced at', value: (d) => d.last_synced_at },
      { header: 'Last sync status', value: (d) => d.last_sync_status },
      { header: 'Last sync duration (s)', value: (d) => d.last_sync_duration_seconds },
      { header: 'Adapter mode', value: (d) => d.adapter_mode },
    ]);
    enqueueSnackbar(`Exported ${dealers.length} dealerships`, { variant: 'success' });
  }

  async function exportInventory() {
    // Page through the full active inventory — a report should not be capped
    // at whatever one page of the Inventory table happened to load.
    const first = await listVehicles({ page: 1, page_size: PAGE_SIZE, sort: 'id', order: 'asc' });
    const items = [...first.items];
    const totalPages = Math.ceil(first.meta.total / PAGE_SIZE);
    for (let page = 2; page <= totalPages; page += 1) {
      const next = await listVehicles({ page, page_size: PAGE_SIZE, sort: 'id', order: 'asc' });
      items.push(...next.items);
    }
    if (items.length === 0) {
      enqueueSnackbar('No vehicles to export', { variant: 'info' });
      return;
    }
    const dealerMap = new Map((await listDealers()).map((d) => [d.id, d.name]));
    downloadCsv(stampedFilename('inventory-full'), items, [
      { header: 'ID', value: (v) => v.id },
      { header: 'Dealer', value: (v) => dealerMap.get(v.dealer_id) ?? v.dealer_id },
      { header: 'Year', value: (v) => v.year },
      { header: 'Make', value: (v) => v.make },
      { header: 'Model', value: (v) => v.model },
      { header: 'Trim', value: (v) => v.trim },
      { header: 'VIN', value: (v) => v.vin },
      { header: 'Price', value: (v) => v.price },
      { header: 'Currency', value: (v) => v.currency },
      { header: 'Mileage', value: (v) => v.mileage },
      { header: 'Status', value: (v) => v.status },
      { header: 'First seen', value: (v) => v.first_seen_at },
      { header: 'Last seen', value: (v) => v.last_seen_at },
      { header: 'URL', value: (v) => v.url },
    ]);
    enqueueSnackbar(`Exported ${items.length.toLocaleString()} vehicles`, { variant: 'success' });
  }

  async function exportRuns() {
    const dealers = await listDealers();
    if (dealers.length === 0) {
      enqueueSnackbar('No dealerships to export runs for', { variant: 'info' });
      return;
    }
    const rows = runsAgg.runs;
    if (rows.length === 0) {
      enqueueSnackbar('No scrape runs recorded yet', { variant: 'info' });
      return;
    }
    downloadCsv(stampedFilename('scrape-runs'), rows, [
      { header: 'Dealer', value: (r) => r.dealer_name },
      { header: 'Started', value: (r) => r.started_at },
      { header: 'Finished', value: (r) => r.finished_at },
      { header: 'Status', value: (r) => r.status },
      { header: 'Scraped', value: (r) => r.scraped_count },
      { header: 'New', value: (r) => r.new_count },
      { header: 'Updated', value: (r) => r.updated_count },
      { header: 'Price changed', value: (r) => r.price_changed_count },
      { header: 'Removed', value: (r) => r.removed_count },
      { header: 'Sold', value: (r) => r.sold_count },
      { header: 'Error', value: (r) => r.error },
    ]);
    enqueueSnackbar(`Exported ${rows.length} scrape runs`, { variant: 'success' });
  }

  const reports: ReportDef[] = [
    {
      id: 'dealers',
      title: 'Dealership directory',
      description: 'Every dealership with its status, provider, contact details and current inventory count.',
      icon: <StorefrontRoundedIcon />,
      color: theme.palette.primary.main,
      run: exportDealers,
    },
    {
      id: 'inventory',
      title: 'Full inventory export',
      description: 'Every vehicle currently tracked across all dealerships, including VIN, price and status.',
      icon: <DirectionsCarFilledRoundedIcon />,
      color: theme.palette.success.main,
      run: exportInventory,
    },
    {
      id: 'runs',
      title: 'Scrape run history',
      description: 'Execution history for every dealership — duration, outcome and counts per run.',
      icon: <BoltRoundedIcon />,
      color: theme.palette.warning.main,
      run: exportRuns,
    },
  ];

  const lastRunAt = dealersQ.data?.reduce<string | null>((latest, d) => {
    if (!d.last_synced_at) return latest;
    if (!latest || new Date(d.last_synced_at) > new Date(latest)) return d.last_synced_at;
    return latest;
  }, null);

  return (
    <Box>
      <PageHeader
        title="Reports"
        description="Export platform data as CSV — ready for a spreadsheet, BI tool or handoff to another team."
      />

      <Surface sx={{ p: 2, mb: 3, display: 'flex', alignItems: 'center', gap: 1.5, bgcolor: alpha(theme.palette.info.main, 0.06), borderColor: alpha(theme.palette.info.main, 0.2) }}>
        <Typography variant="body2" color="text.secondary">
          Data reflects the platform as of now
          {lastRunAt ? ` — most recent sync ${formatDate(lastRunAt)}` : ''}. Each export queries the API fresh, so
          large inventories may take a few seconds to compile.
        </Typography>
      </Surface>

      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: 'repeat(3, 1fr)' }, gap: 2 }}>
        {reports.map((report, i) => (
          <AppearIn key={report.id} index={i}>
            <SectionCard sx={{ height: '100%' }}>
              <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, height: '100%' }}>
                <SoftIconTile color={report.color} size={44}>
                  {report.icon}
                </SoftIconTile>
                <Box sx={{ flex: 1 }}>
                  <Typography variant="h6" sx={{ mb: 0.5 }}>
                    {report.title}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    {report.description}
                  </Typography>
                </Box>
                <Button
                  variant="outlined"
                  startIcon={
                    runningId === report.id ? <CircularProgress size={15} color="inherit" /> : <FileDownloadRoundedIcon />
                  }
                  disabled={runningId !== null}
                  onClick={() => withRunning(report.id, report.run)}
                >
                  {runningId === report.id ? 'Preparing…' : 'Export CSV'}
                </Button>
              </Box>
            </SectionCard>
          </AppearIn>
        ))}
      </Box>
    </Box>
  );
}
