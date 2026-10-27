import { Box, CircularProgress } from '@mui/material';
import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from './AuthContext';

/**
 * Route guard. Wraps the whole app shell, so a new page cannot be added
 * unprotected by accident.
 *
 * This is convenience, not security — the API enforces auth independently, and
 * hiding a route in the browser protects nothing on its own.
 */
export function RequireAuth() {
  const { user, loading } = useAuth();
  const location = useLocation();

  // Wait for the session check. Redirecting while it is still in flight would
  // bounce a signed-in operator to the login page on every refresh.
  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh' }}>
        <CircularProgress />
      </Box>
    );
  }

  if (!user) {
    // Remember where they were headed so login can return them there rather
    // than dumping everyone on the dashboard.
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  return <Outlet />;
}
