import { useMemo, useState } from 'react';
import { Link as RouterLink } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  Alert,
  Box,
  Button,
  Chip,
  FormControlLabel,
  Link,
  Paper,
  Skeleton,
  Stack,
  Switch,
  Tooltip,
  Typography,
} from '@mui/material';
import { DataGrid, type GridColDef } from '@mui/x-data-grid';
import OpenInNewRoundedIcon from '@mui/icons-material/OpenInNewRounded';
import { getCompatibilityReport } from '../api/dealers';
import { getErrorMessage } from '../api/client';
import type { SiteCompatibility } from '../types';

/** Small KPI tile — kept local so this page doesn't couple to the dashboard's cards. */
function Stat({
  label,
  value,
  tone,
  hint,
}: {
  label: string;
  value: number | string;
  tone?: 'default' | 'success' | 'warning' | 'error';
  hint?: string;
}) {
  const color =
    tone === 'success'
      ? 'success.main'
      : tone === 'warning'
        ? 'warning.main'
        : tone === 'error'
          ? 'error.main'
          : 'text.primary';
  return (
    <Tooltip title={hint ?? ''} arrow disableHoverListener={!hint}>
      <Paper variant="outlined" sx={{ p: 2, flex: 1, minWidth: 132 }}>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
          {label}
        </Typography>
        <Typography variant="h5" sx={{ fontWeight: 700, color }}>
          {value}
        </Typography>
      </Paper>
    </Tooltip>
  );
}

function VerdictChip({ verdict }: { verdict: SiteCompatibility['verdict'] }) {
  if (verdict === 'ready') return <Chip size="small" color="success" label="Supported" />;
  if (verdict === 'needs_review') return <Chip size="small" color="warning" label="Needs review" />;
  if (verdict === 'unsupported') return <Chip size="small" color="error" label="Not supported" />;
  return <Chip size="small" variant="outlined" label="Unknown" />;
}

export function CompatibilityPage() {
  const [billableOnly, setBillableOnly] = useState(false);

  const reportQ = useQuery({
    queryKey: ['compatibility', billableOnly],
    queryFn: () => getCompatibilityReport(billableOnly),
  });

  const columns: GridColDef<SiteCompatibility>[] = useMemo(
    () => [
      {
        field: 'host',
        headerName: 'Website',
        flex: 1.3,
        minWidth: 190,
        renderCell: (p) => (
          <Stack sx={{ py: 0.5 }}>
            <Link
              href={p.row.base_url}
              target="_blank"
              rel="noreferrer"
              underline="hover"
              sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5, fontWeight: 600 }}
            >
              {p.row.host}
              <OpenInNewRoundedIcon sx={{ fontSize: 14 }} />
            </Link>
            {p.row.dealer_name && (
              <Typography variant="caption" color="text.secondary">
                {p.row.dealer_name}
              </Typography>
            )}
          </Stack>
        ),
      },
      {
        field: 'verdict',
        headerName: 'Compatibility',
        width: 150,
        renderCell: (p) => <VerdictChip verdict={p.row.verdict} />,
      },
      {
        field: 'requires_custom_work',
        headerName: 'Billable',
        width: 130,
        renderCell: (p) =>
          p.row.requires_custom_work ? (
            <Tooltip
              arrow
              title="This website structure needs custom scraper development before it can be onboarded"
            >
              <Chip size="small" color="error" variant="outlined" label="Quote needed" />
            </Tooltip>
          ) : (
            <Typography variant="body2" color="text.secondary">
              —
            </Typography>
          ),
      },
      {
        field: 'cms_platform',
        headerName: 'Platform',
        width: 170,
        renderCell: (p) =>
          p.row.cms_platform ? (
            <Stack sx={{ py: 0.5 }}>
              <Typography variant="body2">{p.row.cms_platform}</Typography>
              {p.row.hosts_sharing_cms > 1 && (
                <Tooltip
                  arrow
                  title="One scraper already covers this platform — further dealers on it are reuse, not new development"
                >
                  <Typography variant="caption" color="success.main">
                    reused by {p.row.hosts_sharing_cms} sites
                  </Typography>
                </Tooltip>
              )}
            </Stack>
          ) : (
            <Typography variant="body2" color="text.secondary">
              —
            </Typography>
          ),
      },
      {
        field: 'vehicles_verified',
        headerName: 'Vehicles',
        width: 110,
        type: 'number',
      },
      {
        field: 'onboarded',
        headerName: 'Status',
        width: 140,
        renderCell: (p) =>
          p.row.onboarded && p.row.dealer_id ? (
            <Button size="small" component={RouterLink} to={`/dealers/${p.row.dealer_id}`}>
              Onboarded
            </Button>
          ) : (
            <Chip size="small" variant="outlined" label="Evaluated only" />
          ),
      },
      {
        field: 'last_checked_at',
        headerName: 'Last checked',
        width: 160,
        valueFormatter: (value) =>
          value ? new Date(value as string).toLocaleString() : '—',
      },
    ],
    [],
  );

  const summary = reportQ.data?.summary;

  return (
    <Box>
      <Stack
        direction={{ xs: 'column', sm: 'row' }}
        sx={{
          mb: 2,
          gap: 1,
          justifyContent: 'space-between',
          alignItems: { sm: 'center' },
        }}
      >
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 700 }}>
            Site Compatibility
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Which dealer websites our scraper supports, and which need custom development.
          </Typography>
        </Box>
        <FormControlLabel
          control={
            <Switch
              checked={billableOnly}
              onChange={(e) => setBillableOnly(e.target.checked)}
            />
          }
          label="Billable only"
        />
      </Stack>

      {reportQ.isError && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {getErrorMessage(reportQ.error)}
        </Alert>
      )}

      {reportQ.isLoading ? (
        <Stack direction="row" spacing={2} sx={{ mb: 2 }}>
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} variant="rounded" height={84} sx={{ flex: 1 }} />
          ))}
        </Stack>
      ) : (
        summary && (
          <Stack direction="row" spacing={2} sx={{ mb: 2, flexWrap: 'wrap', gap: 2 }}>
            <Stat label="Sites evaluated" value={summary.hosts_total} />
            <Stat label="Supported" value={summary.ready} tone="success" />
            <Stat label="Needs review" value={summary.needs_review} tone="warning" />
            <Stat
              label="Needs custom work"
              value={summary.billable}
              tone="error"
              hint="Quote new-website-structure development for these"
            />
            <Stat label="Onboarded" value={summary.onboarded} />
            <Stat
              label="Platforms"
              value={summary.distinct_cms_platforms}
              hint="Distinct CMS platforms detected — dealers sharing one reuse the same scraper"
            />
          </Stack>
        )
      )}

      {summary && summary.hosts_total === 0 && !reportQ.isLoading && (
        <Alert severity="info">
          No sites evaluated yet. Use <strong>Detect settings</strong> when adding a
          dealership and results will appear here.
        </Alert>
      )}

      <Paper variant="outlined" sx={{ height: 560 }}>
        <DataGrid
          rows={reportQ.data?.items ?? []}
          columns={columns}
          getRowId={(r) => r.host}
          loading={reportQ.isLoading}
          disableRowSelectionOnClick
          initialState={{ pagination: { paginationModel: { pageSize: 25 } } }}
          pageSizeOptions={[25, 50, 100]}
        />
      </Paper>
    </Box>
  );
}
