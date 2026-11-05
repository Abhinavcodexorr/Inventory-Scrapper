import { useMemo, useState } from 'react';
import { Autocomplete, TextField } from '@mui/material';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { getDealer, listDealers } from '../api/dealers';
import { useDebouncedValue } from '../utils/useDebouncedValue';

interface Option {
  id: string;
  name: string;
}

interface Props {
  value: string;
  onChange: (dealerId: string) => void;
  label?: string;
  allLabel?: string;
  width?: number;
}

/** Dealer picker that searches on the server, so it works with any number of dealers. */
export function DealerFilter({ value, onChange, label = 'Dealer', allLabel = 'All', width = 200 }: Props) {
  const [input, setInput] = useState('');
  const [open, setOpen] = useState(false);
  const search = useDebouncedValue(input, 220);

  const selectedQ = useQuery({
    queryKey: ['dealer', Number(value)],
    queryFn: () => getDealer(Number(value)),
    enabled: Boolean(value),
  });
  const selected = useMemo<Option | null>(
    () => (value ? { id: value, name: selectedQ.data?.name ?? `Dealer #${value}` } : null),
    [value, selectedQ.data],
  );

  // The box shows the selected name, which is not something to search for.
  const q = selected && input === selected.name ? '' : search;
  const optionsQ = useQuery({
    queryKey: ['dealers', 'filter', q],
    queryFn: () => listDealers({ q: q || undefined, limit: 20 }),
    enabled: open,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
  const options = useMemo<Option[]>(
    () => (optionsQ.data ?? []).map((d) => ({ id: String(d.id), name: d.name })),
    [optionsQ.data],
  );

  return (
    <Autocomplete
      size="small"
      sx={{ width }}
      open={open}
      onOpen={() => setOpen(true)}
      onClose={() => setOpen(false)}
      options={options}
      value={selected}
      inputValue={input}
      loading={optionsQ.isFetching}
      filterOptions={(all) => all}
      getOptionLabel={(o) => o.name}
      getOptionKey={(o) => o.id}
      isOptionEqualToValue={(a, b) => a.id === b.id}
      onChange={(_, next) => onChange(next?.id ?? '')}
      onInputChange={(_, next) => setInput(next)}
      noOptionsText="No matching dealers"
      renderInput={(params) => <TextField {...params} label={label} placeholder={allLabel} />}
    />
  );
}
