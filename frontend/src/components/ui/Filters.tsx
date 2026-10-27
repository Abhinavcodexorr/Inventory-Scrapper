import type { ReactNode } from 'react';
import {
  Box,
  Button,
  FormControl,
  InputAdornment,
  InputLabel,
  MenuItem,
  Select,
  TextField,
  Typography,
  type SelectChangeEvent,
} from '@mui/material';
import SearchRoundedIcon from '@mui/icons-material/SearchRounded';
import CloseRoundedIcon from '@mui/icons-material/CloseRounded';
import FilterListRoundedIcon from '@mui/icons-material/FilterListRounded';
import { Surface } from './Surface';
import { useTokens } from '../../theme/useTokens';

interface FilterBarProps {
  children: ReactNode;
  /** Number of filters currently applied — drives the reset affordance. */
  activeCount?: number;
  onReset?: () => void;
  /** Right-aligned controls (view switchers, exports). */
  trailing?: ReactNode;
}

/** Toolbar strip above a table: search, filters, reset and view controls. */
export function FilterBar({ children, activeCount = 0, onReset, trailing }: FilterBarProps) {
  const tk = useTokens();

  return (
    <Surface
      sx={{
        display: 'flex',
        alignItems: 'center',
        gap: 1.5,
        p: 1.5,
        mb: 2,
        flexWrap: 'wrap',
        borderRadius: `${tk.radii.control}px`,
      }}
    >
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap', flex: 1, minWidth: 0 }}>
        {children}
      </Box>

      {activeCount > 0 && onReset && (
        <Button
          size="small"
          onClick={onReset}
          startIcon={<CloseRoundedIcon sx={{ fontSize: 15 }} />}
          sx={{ flexShrink: 0 }}
        >
          Clear {activeCount}
        </Button>
      )}

      {trailing && <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexShrink: 0 }}>{trailing}</Box>}
    </Surface>
  );
}

interface SearchFieldProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  width?: number | string;
  'aria-label'?: string;
}

/** Search input with a leading icon and an inline clear affordance. */
export function SearchField({ value, onChange, placeholder = 'Search…', width = 260, ...rest }: SearchFieldProps) {
  return (
    <TextField
      size="small"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      sx={{ width, minWidth: 180, flexGrow: { xs: 1, sm: 0 } }}
      slotProps={{
        htmlInput: { 'aria-label': rest['aria-label'] ?? placeholder },
        input: {
          startAdornment: (
            <InputAdornment position="start">
              <SearchRoundedIcon sx={{ fontSize: 18, color: 'text.disabled' }} />
            </InputAdornment>
          ),
          endAdornment: value ? (
            <InputAdornment position="end">
              <Box
                component="button"
                type="button"
                aria-label="Clear search"
                onClick={() => onChange('')}
                sx={{
                  display: 'grid',
                  placeItems: 'center',
                  width: 18,
                  height: 18,
                  border: 0,
                  p: 0,
                  borderRadius: '50%',
                  cursor: 'pointer',
                  bgcolor: 'action.hover',
                  color: 'text.secondary',
                  '&:hover': { color: 'text.primary' },
                }}
              >
                <CloseRoundedIcon sx={{ fontSize: 12 }} />
              </Box>
            </InputAdornment>
          ) : null,
        },
      }}
    />
  );
}

export interface FilterOption {
  value: string;
  label: string;
}

interface FilterSelectProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: FilterOption[];
  /** Label for the "no filter" entry. */
  allLabel?: string;
  width?: number;
  /** Hide the "all" entry for required selects. */
  required?: boolean;
}

/** Compact labelled select used for every table filter. */
export function FilterSelect({
  label,
  value,
  onChange,
  options,
  allLabel = 'All',
  width = 160,
  required = false,
}: FilterSelectProps) {
  const handle = (e: SelectChangeEvent) => onChange(e.target.value);

  return (
    <FormControl size="small" sx={{ minWidth: width }}>
      <InputLabel>{label}</InputLabel>
      <Select label={label} value={value} onChange={handle}>
        {!required && (
          <MenuItem value="">
            <Typography variant="body2" color="text.secondary">
              {allLabel}
            </Typography>
          </MenuItem>
        )}
        {options.map((opt) => (
          <MenuItem key={opt.value} value={opt.value}>
            {opt.label}
          </MenuItem>
        ))}
      </Select>
    </FormControl>
  );
}

interface NumberRangeProps {
  label: string;
  min: string;
  max: string;
  onMinChange: (value: string) => void;
  onMaxChange: (value: string) => void;
  width?: number;
}

/** Paired min/max numeric inputs sharing one label. */
export function NumberRange({ label, min, max, onMinChange, onMaxChange, width = 76 }: NumberRangeProps) {
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
      <FilterListRoundedIcon sx={{ fontSize: 15, color: 'text.disabled' }} />
      <Typography variant="caption" color="text.secondary" sx={{ mr: 0.5, whiteSpace: 'nowrap' }}>
        {label}
      </Typography>
      <TextField
        size="small"
        type="number"
        value={min}
        onChange={(e) => onMinChange(e.target.value)}
        placeholder="Min"
        slotProps={{ htmlInput: { 'aria-label': `${label} minimum` } }}
        sx={{ width }}
      />
      <Typography variant="caption" color="text.disabled">
        –
      </Typography>
      <TextField
        size="small"
        type="number"
        value={max}
        onChange={(e) => onMaxChange(e.target.value)}
        placeholder="Max"
        slotProps={{ htmlInput: { 'aria-label': `${label} maximum` } }}
        sx={{ width }}
      />
    </Box>
  );
}
