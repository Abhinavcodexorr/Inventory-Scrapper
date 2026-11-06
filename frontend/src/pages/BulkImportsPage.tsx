import { useState } from 'react';
import { Link as RouterLink, useNavigate } from 'react-router-dom';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { Box, Button, IconButton, Tooltip, Typography } from '@mui/material';
import { useTheme } from '@mui/material/styles';
import ArrowBackRoundedIcon from '@mui/icons-material/ArrowBackRounded';
import DownloadRoundedIcon from '@mui/icons-material/DownloadRounded';
import AutorenewRoundedIcon from '@mui/icons-material/AutorenewRounded';
import HourglassEmptyRoundedIcon from '@mui/icons-material/HourglassEmptyRounded';
import ListAltRoundedIcon from '@mui/icons-material/ListAltRounded';
import UploadFileRoundedIcon from '@mui/icons-material/UploadFileRounded';
import { listBulkImportsPage } from '../api/bulkImports';
import { DataTable, EmptyState, PageHeader, SectionCard, StatCard } from '../components/ui';
import { useBulkActivity } from '../hooks/useBulkActivity';
import { bulkImportColumns } from './bulkImportColumns';

export function BulkImportsPage() {
  const theme = useTheme();
  const navigate = useNavigate();
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(25);

  const activityQ = useBulkActivity();
  const activity = activityQ.data;
  const anyRunning = (activity?.running_imports ?? 0) > 0;

  const importsQ = useQuery({
    queryKey: ['bulk-imports', 'page', { page, pageSize }],
    queryFn: () => listBulkImportsPage({ limit: pageSize, offset: page * pageSize }),
    placeholderData: keepPreviousData,
    refetchInterval: anyRunning ? 5_000 : 30_000,
  });
  const total = importsQ.data?.total ?? 0;

  return (
    <Box>
      <PageHeader
        leading={
          <Tooltip title="Back to dealerships">
            <IconButton component={RouterLink} to="/dealers" aria-label="Back to dealerships" sx={{ mt: 0.5 }}>
              <ArrowBackRoundedIcon />
            </IconButton>
          </Tooltip>
        }
        title="Past imports"
        description="Every bulk list that has been added, with where it stands. Open one to see the result for each website."
        actions={
          <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
            <Button
              variant="outlined"
              component="a"
              href="/bulk-dealers-template.csv"
              download="dealer-websites-format.csv"
              startIcon={<DownloadRoundedIcon />}
            >
              Download CSV format
            </Button>
            <Button variant="contained" component={RouterLink} to="/dealers/bulk" startIcon={<UploadFileRoundedIcon />}>
              Bulk add
            </Button>
          </Box>
        }
      />

      <Box
        sx={{
          display: 'grid',
          gap: 2,
          mb: 1,
          gridTemplateColumns: { xs: 'repeat(1, 1fr)', sm: 'repeat(3, 1fr)' },
        }}
      >
        <StatCard
          label="In process"
          value={activity?.checking ?? 0}
          icon={<AutorenewRoundedIcon />}
          color={theme.palette.info.main}
          loading={activityQ.isLoading}
        />
        <StatCard
          label="Waiting"
          value={activity?.waiting ?? 0}
          icon={<HourglassEmptyRoundedIcon />}
          color={theme.palette.warning.main}
          loading={activityQ.isLoading}
        />
        <StatCard
          label="Lists running"
          value={activity?.running_imports ?? 0}
          icon={<ListAltRoundedIcon />}
          color={theme.palette.primary.main}
          loading={activityQ.isLoading}
        />
      </Box>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 2 }}>
        {activity
          ? anyRunning
            ? `Totals across all running lists. Up to ${activity.concurrency} websites are checked at once; the rest wait their turn.`
            : 'Nothing is being checked right now.'
          : ' '}
      </Typography>

      {!importsQ.isLoading && total === 0 ? (
        <EmptyState
          title="No imports yet"
          description="Add many dealerships at once from a CSV or a pasted list of websites."
          action={
            <Button variant="contained" component={RouterLink} to="/dealers/bulk" startIcon={<UploadFileRoundedIcon />}>
              Bulk add
            </Button>
          }
        />
      ) : (
        <SectionCard flush>
          <DataTable
            rows={importsQ.data?.imports ?? []}
            rowCount={total}
            columns={bulkImportColumns}
            getRowId={(j) => j.id}
            loading={importsQ.isLoading}
            onRowClick={(j) => navigate(`/dealers/bulk/${j.id}`)}
            page={page}
            pageSize={pageSize}
            onPageChange={setPage}
            onPageSizeChange={(size) => {
              setPageSize(size);
              setPage(0);
            }}
            storageKey="bulk-imports"
            rowNoun="imports"
          />
        </SectionCard>
      )}
    </Box>
  );
}
