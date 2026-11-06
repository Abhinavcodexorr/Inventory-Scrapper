import type { BulkRow } from '../types';
import { parseCsv } from './csv';

const COLUMN_ALIASES: Record<string, keyof BulkRow> = {
  website: 'website',
  'website url': 'website',
  url: 'website',
  site: 'website',
  domain: 'website',
  name: 'name',
  'dealer name': 'name',
  dealership: 'name',
  address: 'address',
  city: 'city',
  province: 'province',
  state: 'province',
  postal_code: 'postal_code',
  'postal code': 'postal_code',
  phone: 'phone',
  provider: 'provider',
  'website provider': 'provider',
};

export interface ParsedList {
  rows: BulkRow[];
  /** Template sample lines, left out so they are never imported by accident. */
  samplesIgnored: number;
}

/**
 * Read a list as the template's columns, in any order, or as bare addresses (one
 * per line, or the first column of a sheet with no recognised header).
 */
export function parseBulkList(text: string): ParsedList {
  const grid = parseCsv(text).filter((cells) => cells.some((c) => c.trim()));
  if (grid.length === 0) return { rows: [], samplesIgnored: 0 };

  const header = grid[0].map((c) => COLUMN_ALIASES[c.trim().toLowerCase()]);
  const hasHeader = header.includes('website');
  const lines = hasHeader ? grid.slice(1) : grid;

  const all = lines.map((cells) => {
    const row: BulkRow = { website: '' };
    if (!hasHeader) {
      row.website = cells[0].trim();
      return row;
    }
    header.forEach((field, i) => {
      const value = cells[i]?.trim();
      if (field && value) row[field] = value;
    });
    return row;
  });
  // ".example" is reserved and can never be a real website, so this cannot drop one.
  const rows = all.filter((r) => !/\.example(?:[/:?#]|$)/i.test(r.website));
  return { rows, samplesIgnored: all.length - rows.length };
}
