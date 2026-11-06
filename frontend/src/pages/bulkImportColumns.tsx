import { Typography } from '@mui/material';
import { StatusChip, type Column } from '../components/ui';
import type { BulkImport } from '../types';
import { formatRelative } from '../utils/format';

const count = (n: number) => (n > 0 ? n.toLocaleString() : <Typography component="span" color="text.disabled">0</Typography>);

/** One row per bulk list: what it was, where it stands, and how far it got. */
export const bulkImportColumns: Column<BulkImport>[] = [
  { key: 'id', header: '#', width: 70, value: (j) => j.id },
  { key: 'created', header: 'Started', render: (j) => formatRelative(j.created_at) },
  { key: 'list', header: 'List', minWidth: 160, render: (j) => j.filename || 'Pasted list' },
  { key: 'provider', header: 'Provider', render: (j) => j.provider || 'Auto-detect' },
  { key: 'status', header: 'Status', render: (j) => <StatusChip status={j.status} /> },
  { key: 'total', header: 'Websites', align: 'right', value: (j) => j.total },
  { key: 'checking', header: 'In process', align: 'right', render: (j) => count(j.checking) },
  { key: 'waiting', header: 'Waiting', align: 'right', render: (j) => count(j.waiting) },
  { key: 'added', header: 'Added', align: 'right', render: (j) => count(j.added) },
  { key: 'failed', header: 'Failed', align: 'right', render: (j) => count(j.failed) },
  { key: 'duplicate', header: 'Already existed', align: 'right', render: (j) => count(j.duplicate) },
];
