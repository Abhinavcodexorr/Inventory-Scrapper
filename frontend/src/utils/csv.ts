/** Minimal client-side CSV export — no dependency, no server round trip. */

export interface CsvColumn<T> {
  header: string;
  value: (row: T) => string | number | null | undefined;
}

/**
 * Escape a single field. Anything containing a delimiter, quote or newline gets
 * wrapped, and a leading =/+/-/@ is prefixed with a quote so spreadsheets do not
 * evaluate scraped text as a formula.
 */
function escapeField(raw: string | number | null | undefined): string {
  if (raw == null) return '';
  let value = String(raw);
  if (/^[=+\-@]/.test(value)) value = `'${value}`;
  if (/[",\n\r]/.test(value)) return `"${value.replace(/"/g, '""')}"`;
  return value;
}

export function toCsv<T>(rows: readonly T[], columns: CsvColumn<T>[]): string {
  const head = columns.map((c) => escapeField(c.header)).join(',');
  const body = rows.map((row) => columns.map((c) => escapeField(c.value(row))).join(','));
  return [head, ...body].join('\r\n');
}

/** Trigger a browser download for the given CSV text. */
export function downloadCsv<T>(filename: string, rows: readonly T[], columns: CsvColumn<T>[]): void {
  // The BOM makes Excel read the file as UTF-8 rather than the system codepage.
  const blob = new Blob(['﻿', toCsv(rows, columns)], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename.endsWith('.csv') ? filename : `${filename}.csv`;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

/** `inventory-2026-08-05.csv` — stable, sortable filenames. */
export function stampedFilename(prefix: string): string {
  const now = new Date();
  const stamp = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
  return `${prefix}-${stamp}.csv`;
}

/** Rows of a CSV document: quoted fields, doubled quotes, CRLF and a leading BOM. */
export function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = '';
  let quoted = false;
  const src = text.replace(/^﻿/, '');
  for (let i = 0; i < src.length; i++) {
    const ch = src[i];
    if (quoted) {
      if (ch === '"' && src[i + 1] === '"') {
        field += '"';
        i++;
      } else if (ch === '"') {
        quoted = false;
      } else {
        field += ch;
      }
    } else if (ch === '"' && field === '') {
      quoted = true;
    } else if (ch === ',') {
      row.push(field);
      field = '';
    } else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && src[i + 1] === '\n') i++;
      row.push(field);
      rows.push(row);
      row = [];
      field = '';
    } else {
      field += ch;
    }
  }
  if (field !== '' || row.length > 0) {
    row.push(field);
    rows.push(row);
  }
  return rows;
}
