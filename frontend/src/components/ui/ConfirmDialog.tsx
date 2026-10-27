import type { ReactNode } from 'react';
import {
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Typography,
} from '@mui/material';
import WarningAmberRoundedIcon from '@mui/icons-material/WarningAmberRounded';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import { SoftIconTile } from './SoftIconTile';
import { useTheme } from '@mui/material/styles';

interface Props {
  open: boolean;
  title: ReactNode;
  description?: ReactNode;
  /** Extra content between description and actions. */
  children?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  /** `danger` styles the confirm button as destructive. */
  tone?: 'danger' | 'primary';
  loading?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}

/** Blurred-backdrop confirmation modal with a sticky action footer. */
export function ConfirmDialog({
  open,
  title,
  description,
  children,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  tone = 'primary',
  loading = false,
  onConfirm,
  onClose,
}: Props) {
  const theme = useTheme();
  const accent = tone === 'danger' ? theme.palette.error.main : theme.palette.primary.main;

  return (
    <Dialog
      open={open}
      onClose={loading ? undefined : onClose}
      maxWidth="xs"
      fullWidth
      slotProps={{ transition: { timeout: { enter: 220, exit: 140 } } }}
    >
      <DialogTitle sx={{ display: 'flex', gap: 2, alignItems: 'flex-start', pb: 1 }}>
        <SoftIconTile color={accent} size={40}>
          {tone === 'danger' ? <WarningAmberRoundedIcon /> : <InfoOutlinedIcon />}
        </SoftIconTile>
        <Box sx={{ pt: 0.5 }}>{title}</Box>
      </DialogTitle>

      <DialogContent>
        {description && (
          <Typography variant="body2" color="text.secondary" sx={{ pl: 7 }}>
            {description}
          </Typography>
        )}
        {children && <Box sx={{ pl: 7, mt: 2 }}>{children}</Box>}
      </DialogContent>

      <DialogActions>
        <Box sx={{ flex: 1 }} />
        <Button onClick={onClose} disabled={loading}>
          {cancelLabel}
        </Button>
        <Button
          variant="contained"
          color={tone === 'danger' ? 'error' : 'primary'}
          onClick={onConfirm}
          disabled={loading}
          startIcon={loading ? <CircularProgress size={15} color="inherit" /> : undefined}
        >
          {confirmLabel}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
