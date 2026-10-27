import { forwardRef } from 'react';
import { Box, IconButton, Typography } from '@mui/material';
import { alpha, useTheme } from '@mui/material/styles';
import CheckCircleRoundedIcon from '@mui/icons-material/CheckCircleRounded';
import ErrorRoundedIcon from '@mui/icons-material/ErrorRounded';
import WarningRoundedIcon from '@mui/icons-material/WarningRounded';
import InfoRoundedIcon from '@mui/icons-material/InfoRounded';
import CloseRoundedIcon from '@mui/icons-material/CloseRounded';
import { SnackbarContent, useSnackbar, type CustomContentProps } from 'notistack';
import { useTokens } from '../theme/useTokens';

/**
 * Custom toast body used for every notistack variant.
 *
 * Replaces Material's stock snackbar with a card that matches the rest of the
 * surface language: tinted status icon, layered shadow, explicit dismiss.
 */
export const Toast = forwardRef<HTMLDivElement, CustomContentProps>(function Toast(props, ref) {
  const { id, message, variant, action } = props;
  const theme = useTheme();
  const tk = useTokens();
  const { closeSnackbar } = useSnackbar();

  const accent =
    variant === 'success'
      ? theme.palette.success.main
      : variant === 'error'
        ? theme.palette.error.main
        : variant === 'warning'
          ? theme.palette.warning.main
          : theme.palette.info.main;

  const Icon =
    variant === 'success'
      ? CheckCircleRoundedIcon
      : variant === 'error'
        ? ErrorRoundedIcon
        : variant === 'warning'
          ? WarningRoundedIcon
          : InfoRoundedIcon;

  return (
    <SnackbarContent ref={ref} role="alert">
      <Box
        sx={{
          display: 'flex',
          alignItems: 'flex-start',
          gap: 1.5,
          width: '100%',
          minWidth: 300,
          maxWidth: 440,
          px: 2,
          py: 1.5,
          borderRadius: `${tk.radii.control}px`,
          border: `1px solid ${tk.surfaces.border}`,
          bgcolor: tk.surfaces.raised,
          boxShadow: tk.elevation.lg,
        }}
      >
        <Box
          aria-hidden="true"
          sx={{
            width: 24,
            height: 24,
            flexShrink: 0,
            mt: 0.125,
            borderRadius: '50%',
            display: 'grid',
            placeItems: 'center',
            color: accent,
            bgcolor: alpha(accent, theme.palette.mode === 'light' ? 0.12 : 0.2),
          }}
        >
          <Icon sx={{ fontSize: 15 }} />
        </Box>

        <Typography variant="body2" sx={{ flex: 1, minWidth: 0, color: 'text.primary', fontWeight: 500, pt: 0.125 }}>
          {message}
        </Typography>

        {typeof action === 'function' ? action(id) : action}

        <IconButton
          size="small"
          onClick={() => closeSnackbar(id)}
          aria-label="Dismiss notification"
          sx={{ mt: -0.5, mr: -0.75 }}
        >
          <CloseRoundedIcon sx={{ fontSize: 15 }} />
        </IconButton>
      </Box>
    </SnackbarContent>
  );
});
