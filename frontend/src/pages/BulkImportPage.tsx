import { useMemo, useRef, useState } from 'react';
import { Link as RouterLink, useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Autocomplete, Box, Button, IconButton, TextField, Tooltip, Typography } from '@mui/material';
import ArrowBackRoundedIcon from '@mui/icons-material/ArrowBackRounded';
import DownloadRoundedIcon from '@mui/icons-material/DownloadRounded';
import UploadFileRoundedIcon from '@mui/icons-material/UploadFileRounded';
import { useSnackbar } from 'notistack';
import { getErrorMessage } from '../api/client';
import { listProviders } from '../api/dealers';
import { createBulkImport, listBulkImports, previewBulkImport } from '../api/bulkImports';
import { DataTable, PageHeader, SectionCard, StatusChip, type Column } from '../components/ui';
import type { BulkImport, BulkPreview, BulkPreviewRow } from '../types';
import { parseBulkList } from '../utils/bulkList';
import { formatRelative } from '../utils/format';

const MAX_ROWS = 5000;

const previewColumns: Column<BulkPreviewRow>[] = [
  { key: 'position', header: 'Row', width: 70, value: (r) => r.position },
  { key: 'website', header: 'Website', minWidth: 220, value: (r) => r.website },
  { key: 'name', header: 'Name', value: (r) => r.name },
  { key: 'provider', header: 'Provider', value: (r) => r.provider },
  {
    key: 'status',
    header: 'Result',
    render: (r) => <StatusChip status={r.status === 'queued' ? 'ready' : r.status} />,
  },
  { key: 'reason', header: 'Note', minWidth: 220, render: (r) => r.reason ?? '' },
];

const historyColumns: Column<BulkImport>[] = [
  { key: 'created', header: 'Started', render: (j) => formatRelative(j.created_at) },
  { key: 'list', header: 'List', render: (j) => j.filename || 'Pasted list' },
  { key: 'provider', header: 'Provider', render: (j) => j.provider || 'Auto-detect' },
  { key: 'status', header: 'Status', render: (j) => <StatusChip status={j.status} /> },
  { key: 'total', header: 'Websites', align: 'right', value: (j) => j.total },
  { key: 'added', header: 'Added', align: 'right', value: (j) => j.added },
  { key: 'failed', header: 'Failed', align: 'right', value: (j) => j.failed },
];

export function BulkImportPage() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { enqueueSnackbar } = useSnackbar();
  const fileInput = useRef<HTMLInputElement>(null);

  const [provider, setProvider] = useState('');
  const [text, setText] = useState('');
  const [filename, setFilename] = useState<string | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [preview, setPreview] = useState<BulkPreview | null>(null);
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(25);

  const providersQ = useQuery({ queryKey: ['providers'], queryFn: listProviders });
  const historyQ = useQuery({
    queryKey: ['bulk-imports', 'history'],
    queryFn: () => listBulkImports(20),
    refetchInterval: 15_000,
  });

  const parsed = useMemo(() => parseBulkList(text), [text]);
  const tooMany = parsed.rows.length > MAX_ROWS;
  const payload = () => ({ provider: provider.trim() || null, filename, rows: parsed.rows });

  const previewM = useMutation({
    mutationFn: () => previewBulkImport(payload()),
    onSuccess: (result) => {
      setPreview(result);
      setPage(0);
    },
    onError: (e) => enqueueSnackbar(getErrorMessage(e), { variant: 'error' }),
  });

  const startM = useMutation({
    mutationFn: () => createBulkImport(payload()),
    onSuccess: (job) => {
      qc.invalidateQueries({ queryKey: ['bulk-imports'] });
      navigate(`/dealers/bulk/${job.id}`);
    },
    onError: (e) => enqueueSnackbar(getErrorMessage(e), { variant: 'error' }),
  });

  function changeList(value: string, name: string | null) {
    setText(value);
    setFilename(name);
    setPreview(null);
  }

  async function onFile(file: File | undefined) {
    if (!file) return;
    setFileError(null);
    if (/\.(xlsx?|xlsm|ods|numbers)$/i.test(file.name)) {
      setFileError('This is a spreadsheet file. In Excel, use Save As → CSV, then upload the CSV.');
      return;
    }
    changeList(await file.text(), file.name);
  }

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
        title="Bulk add dealerships"
        description="Add many websites at once. Each one is checked in the background and added if vehicles are found, then you get a report."
        actions={
          <Button
            variant="outlined"
            component="a"
            href="/bulk-dealers-template.csv"
            download
            startIcon={<DownloadRoundedIcon />}
          >
            Download template
          </Button>
        }
      />

      <SectionCard title="Your list" sx={{ mb: 2 }}>
        <Box sx={{ display: 'grid', gap: 2 }}>
          <Autocomplete
            freeSolo
            options={[...(providersQ.data ?? [])].sort()}
            value={provider}
            onChange={(_, value) => setProvider(value ?? '')}
            onInputChange={(_, value) => setProvider(value)}
            renderInput={(params) => (
              <TextField
                {...params}
                label="Website provider"
                placeholder="e.g. HillzDealer"
                helperText="Saved on every website in this list. A provider column in the file overrides it for that row. Leave blank to use the platform the check detects."
              />
            )}
          />
          <Box sx={{ display: 'flex', gap: 1.5, alignItems: 'center', flexWrap: 'wrap' }}>
            <input
              ref={fileInput}
              type="file"
              accept=".csv,text/csv,text/plain"
              hidden
              onChange={(e) => {
                void onFile(e.target.files?.[0]);
                e.target.value = '';
              }}
            />
            <Button variant="outlined" startIcon={<UploadFileRoundedIcon />} onClick={() => fileInput.current?.click()}>
              Upload CSV
            </Button>
            <Typography variant="body2" color="text.secondary">
              {filename ? `Loaded ${filename}` : 'or paste addresses below, one per line'}
            </Typography>
          </Box>
          {fileError && <Alert severity="warning">{fileError}</Alert>}
          <TextField
            multiline
            minRows={6}
            maxRows={14}
            label="Websites"
            placeholder={'cardora.ca\nhttps://www.bluestarmotors.com'}
            value={text}
            onChange={(e) => changeList(e.target.value, null)}
          />
          {parsed.samplesIgnored > 0 && (
            <Alert severity="info">
              Ignored {parsed.samplesIgnored} sample row{parsed.samplesIgnored === 1 ? '' : 's'} from the template.
            </Alert>
          )}
          {tooMany && (
            <Alert severity="warning">
              This list has {parsed.rows.length.toLocaleString()} websites. The limit is{' '}
              {MAX_ROWS.toLocaleString()} per import, so split it into smaller files.
            </Alert>
          )}
          <Box>
            <Button
              variant="contained"
              disabled={parsed.rows.length === 0 || tooMany || previewM.isPending}
              onClick={() => previewM.mutate()}
            >
              {previewM.isPending
                ? 'Checking list…'
                : `Review ${parsed.rows.length.toLocaleString()} website${parsed.rows.length === 1 ? '' : 's'}`}
            </Button>
          </Box>
        </Box>
      </SectionCard>

      {preview && (
        <SectionCard
          flush
          title={`${preview.ready.toLocaleString()} ready to check`}
          description={`${preview.duplicate.toLocaleString()} already exist or are repeated · ${preview.invalid.toLocaleString()} invalid. Only the ready websites are checked; the others are listed in the report.`}
          actions={
            <Button
              variant="contained"
              disabled={preview.ready === 0 || startM.isPending}
              onClick={() => startM.mutate()}
            >
              {startM.isPending ? 'Starting…' : 'Start import'}
            </Button>
          }
          sx={{ mb: 2 }}
        >
          <DataTable
            rows={preview.rows}
            columns={previewColumns}
            getRowId={(r) => r.position}
            page={page}
            pageSize={pageSize}
            onPageChange={setPage}
            onPageSizeChange={(size) => {
              setPageSize(size);
              setPage(0);
            }}
            pageSizeOptions={[25, 50, 100]}
            storageKey="bulk-preview"
            rowNoun="websites"
          />
        </SectionCard>
      )}

      {(historyQ.data?.length ?? 0) > 0 && (
        <SectionCard flush title="Past imports">
          <DataTable
            rows={historyQ.data ?? []}
            columns={historyColumns}
            getRowId={(j) => j.id}
            onRowClick={(j) => navigate(`/dealers/bulk/${j.id}`)}
            storageKey="bulk-history"
            rowNoun="imports"
          />
        </SectionCard>
      )}
    </Box>
  );
}
