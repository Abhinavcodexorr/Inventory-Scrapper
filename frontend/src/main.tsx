import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { SnackbarProvider } from 'notistack';
import App from './App';
import { AppThemeProvider } from './theme/AppThemeProvider';
import { Toast } from './components/Toast';
import './index.css';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <AppThemeProvider>
        <SnackbarProvider
          maxSnack={4}
          autoHideDuration={4500}
          anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
          Components={{ default: Toast, success: Toast, error: Toast, warning: Toast, info: Toast }}
        >
          <App />
        </SnackbarProvider>
      </AppThemeProvider>
    </QueryClientProvider>
  </StrictMode>,
);
