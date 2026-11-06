import { useEffect, useState } from 'react';
import { Link as RouterLink, useNavigate, useParams } from 'react-router-dom';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Box, Button, IconButton, LinearProgress, Skeleton, Tooltip, Typography } from '@mui/material';
import { useTheme } from '@mui/material/styles';
import ArrowBackRoundedIcon from '@mui/icons-material/ArrowBackRounded';
import CheckCircleRoundedIcon from '@mui/icons-material/CheckCircleRounded';
import DownloadRoundedIcon from '@mui/icons-material/DownloadRounded';
import ErrorRoundedIcon from '@mui/icons-material/ErrorRounded';
import HourglassEmptyRoundedIcon from '@mui/icons-material/HourglassEmptyRounded';
import ContentCopyRoundedIcon from '@mui/icons-material/ContentCopyRounded';
import { useSnackbar } from 'notistack';
import { getErrorMessage } from '../api/client';
import {
  getBulkImport,
  listBulkImportRows,
  markBulkReportSeen,
  retryBulkImport,
  stopBulkImport,
} from '../api/bulkImports';
import {
  DataTable,
  EmptyState,
  ExpandableError,
  FilterBar,
  FilterSelect,
  PageHeader,
  SectionCard,
  StatCard,
  StatusChip,
  type Column,
} from '../components/ui';
import type { BulkImportRowResult } from '../types';
import { downloadCsv, stampedFilename } from '../utils/csv';

const STATUS_LABELS: Record<string, string> = {
  queued: 'Waiting',
  checking: 'Checking',
  added: 'Added',
  failed: 'Failed',
  invalid: 'Invalid address',
  duplicate: 'Already exists',
  skipped: 'Skipped',
};

const FILTERS = [
  { value: 'added', label: 'Added' },
  { value: 'failed', label: 'Failed' },
  { value: 'duplicate', label: 'Already exists' },
  { value: 'skipped', label: 'Skipped' },
  { value: 'queued', label: 'Waiting' },
  { value: 'checking', label: 'Checking' },
];

const columns: Column<BulkImportRowResult>[] = [
  { key: 'position', header: 'Row', width: 70, value: (r) => r.position },
  { key: 'website', header: 'Website', minWidth: 180, value: (r) => r.website },
  { key: 'name', header: 'Name', value: (r) => r.name },
  { key: 'provider', header: 'Provider', value: (r) => r.provider },
  {
    key: 'status',
    header: 'Result',
    render: (r) => <StatusChip status={r.status === 'queued' ? 'waiting' : r.status} />,
  },
  { key: 'vehicles', header: 'Vehicles', align: 'right', value: (r) => r.vehicles_found },
  { key: 'reason', header: 'Reason', minWidth: 200, render: (r) => (r.reason ? <ExpandableError text={r.reason} /> : '') },
];

export function BulkImportReportPage() {
  const theme = useTheme();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { enqueueSnackbar } = useSnackbar();
  const importId = Number(useParams().id);

  const [filter, setFilter] = useState('');
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(50);

  const isLive = (status?: string) => status === 'running' || status === 'stopping';

  const jobQ = useQuery({
    queryKey: ['bulk-imports', importId],
    queryFn: () => getBulkImport(importId),
    refetchInterval: (q) => (isLive(q.state.data?.status) ? 5000 : false),
  });
  const job = jobQ.data;
  const live = isLive(job?.status);

  const rowsQ = useQuery({
    queryKey: ['bulk-imports', importId, 'rows', { filter, page, pageSize }],
    queryFn: () =>
      listBulkImportRows(importId, { status: filter || undefined, limit: pageSize, offset: page * pageSize }),
    placeholderData: keepPreviousData,
    refetchInterval: live ? 5000 : false,
  });

  useEffect(() => {
    if (job?.finished_at) markBulkReportSeen(job.id);
  }, [job?.id, job?.finished_at]);

  // The final poll can land before the last rows are read; refresh once at the end.
  useEffect(() => {
    if (job?.finished_at) qc.invalidateQueries({ queryKey: ['bulk-imports', importId, 'rows'] });
  }, [job?.finished_at, importId, qc]);

  const onActionDone = () => qc.invalidateQueries({ queryKey: ['bulk-imports'] });
  const onActionError = (e: unknown) => enqueueSnackbar(getErrorMessage(e), { variant: 'error' });
  const stopM = useMutation({ mutationFn: () => stopBulkImport(importId), onSuccess: onActionDone, onError: onActionError });
  const retryM = useMutation({ mutationFn: () => retryBulkImport(importId), onSuccess: onActionDone, onError: onActionError });

  const exportM = useMutation({
    mutationFn: () => listBulkImportRows(importId, { limit: 5000 }),
    onSuccess: ({ rows }) =>
      downloadCsv(stampedFilename(`bulk-import-${importId}`), rows, [
        { header: 'Row', value: (r) => r.position },
        { header: 'Website', value: (r) => r.website },
        { header: 'Name', value: (r) => r.name },
        { header: 'Provider', value: (r) => r.provider },
        { header: 'Result', value: (r) => STATUS_LABELS[r.status] ?? r.status },
        { header: 'Vehicles found', value: (r) => r.vehicles_found },
        { header: 'Reason', value: (r) => r.reason },
      ]),
    onError: onActionError,
  });

  if (jobQ.isLoading) return <Skeleton variant="rectangular" height={320} sx={{ borderRadius: 4 }} />;
  if (!job) {
    return <EmptyState title="Import not found" description="It may have been removed." />;
  }

  const done = job.total - job.waiting - job.checking;
  const retryable = job.failed + job.skipped > 0 && !live;

  return (
    <Box>
      <PageHeader
        leading={
          <Tooltip title="Back to bulk add">
            <IconButton component={RouterLink} to="/dealers/bulk" aria-label="Back to bulk add" sx={{ mt: 0.5 }}>
              <ArrowBackRoundedIcon />
            </IconButton>
          </Tooltip>
        }
        title={`Bulk import #${job.id}`}
        description={`${job.filename || 'Pasted list'} · Provider: ${job.provider || 'auto-detect'}`}
        actions={
          <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
            {job.status === 'running' && (
              <Button variant="outlined" color="error" disabled={stopM.isPending} onClick={() => stopM.mutate()}>
                Stop import
              </Button>
            )}
            {retryable && (
              <Button variant="outlined" disabled={retryM.isPending} onClick={() => retryM.mutate()}>
                Retry failed
              </Button>
            )}
            <Button
              variant="contained"
              startIcon={<DownloadRoundedIcon />}
              disabled={exportM.isPending}
              onClick={() => exportM.mutate()}
            >
              Download report
            </Button>
          </Box>
        }
      />

      {job.notice && <Alert severity="warning" sx={{ mb: 2 }}>{job.notice}</Alert>}
      {live ? (
        <SectionCard sx={{ mb: 2 }}>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 1, gap: 2 }}>
            <Typography variant="body2" sx={{ fontWeight: 560 }}>
              {job.status === 'stopping'
                ? 'Stopping: finishing the websites already being checked.'
                : `Checked ${done.toLocaleString()} of ${job.total.toLocaleString()} websites`}
            </Typography>
            <StatusChip status={job.status} />
          </Box>
          <LinearProgress variant="determinate" value={job.total ? (done / job.total) * 100 : 0} />
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
            This runs in the background, so you can close this page. Each website can take up to 5 minutes.
            The bell icon tells you when the report is ready.
          </Typography>
        </SectionCard>
      ) : (
        <Alert severity={job.failed > 0 || job.skipped > 0 ? 'warning' : 'success'} sx={{ mb: 2 }}>
          {job.status === 'stopped' ? 'Import stopped' : 'Import finished'}: {job.added.toLocaleString()} added,{' '}
          {job.failed.toLocaleString()} failed, {job.duplicate.toLocaleString()} already existed
          {job.skipped > 0 ? `, ${job.skipped.toLocaleString()} skipped` : ''}, out of {job.total.toLocaleString()}.
          {job.added > 0 ? ' New dealerships get their vehicles on the next daily sync.' : ''}
        </Alert>
      )}

      <Box
        sx={{
          display: 'grid',
          gap: 2,
          mb: 2,
          gridTemplateColumns: { xs: 'repeat(2, 1fr)', md: 'repeat(4, 1fr)' },
        }}
      >
        <StatCard label="Added" value={job.added} icon={<CheckCircleRoundedIcon />} color={theme.palette.success.main} />
        <StatCard label="Failed" value={job.failed} icon={<ErrorRoundedIcon />} color={theme.palette.error.main} />
        <StatCard label="Already exists" value={job.duplicate} icon={<ContentCopyRoundedIcon />} color={theme.palette.info.main} />
        <StatCard
          label={live ? 'Still to do' : 'Skipped'}
          value={live ? job.waiting + job.checking : job.skipped}
          icon={<HourglassEmptyRoundedIcon />}
          color={theme.palette.warning.main}
        />
      </Box>

      <FilterBar
        activeCount={filter ? 1 : 0}
        onReset={() => {
          setFilter('');
          setPage(0);
        }}
      >
        <FilterSelect
          label="Result"
          value={filter}
          options={FILTERS}
          onChange={(value) => {
            setFilter(value);
            setPage(0);
          }}
        />
      </FilterBar>

      <SectionCard flush>
        <DataTable
          rows={rowsQ.data?.rows ?? []}
          rowCount={rowsQ.data?.total ?? 0}
          columns={columns}
          getRowId={(r) => r.id}
          loading={rowsQ.isLoading}
          onRowClick={(r) => {
            if (r.dealer_id) navigate(`/dealers/${r.dealer_id}`);
          }}
          page={page}
          pageSize={pageSize}
          onPageChange={setPage}
          onPageSizeChange={(size) => {
            setPageSize(size);
            setPage(0);
          }}
          pageSizeOptions={[25, 50, 100]}
          storageKey="bulk-report"
          rowNoun="websites"
        />
      </SectionCard>
    </Box>
  );
}
