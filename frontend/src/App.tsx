import { Suspense, lazy } from 'react';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { AuthProvider } from './auth/AuthContext';
import { RequireAuth } from './auth/RequireAuth';
import { AppLayout } from './layout/AppLayout';
import { PageFallback } from './layout/PageFallback';
import { LoginPage } from './pages/LoginPage';
import { DashboardPage } from './pages/DashboardPage';
import { DealersPage } from './pages/DealersPage';
import { DealerFormPage } from './pages/DealerFormPage';
import { DealerDetailPage } from './pages/DealerDetailPage';
import { VehiclesPage } from './pages/VehiclesPage';
import { VehicleDetailPage } from './pages/VehicleDetailPage';
import { JobsPage } from './pages/JobsPage';

// Chart-heavy routes are split out so the initial bundle doesn't carry the
// whole charting library for users who land on the dashboard and stop there.
const AnalyticsPage = lazy(() => import('./pages/AnalyticsPage').then((m) => ({ default: m.AnalyticsPage })));
const ChangesPage = lazy(() => import('./pages/ChangesPage').then((m) => ({ default: m.ChangesPage })));
const ReportsPage = lazy(() => import('./pages/ReportsPage').then((m) => ({ default: m.ReportsPage })));
const SettingsPage = lazy(() => import('./pages/SettingsPage').then((m) => ({ default: m.SettingsPage })));
const CompatibilityPage = lazy(() =>
  import('./pages/CompatibilityPage').then((m) => ({ default: m.CompatibilityPage })),
);

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        {/* Everything below RequireAuth needs a session. Nesting the guard
            above AppLayout means a newly added page is protected by default
            rather than by remembering to wrap it. */}
        <Route element={<RequireAuth />}>
        <Route element={<AppLayout />}>
          <Route index element={<DashboardPage />} />
          <Route path="dealers" element={<DealersPage />} />
          <Route path="dealers/new" element={<DealerFormPage />} />
          <Route path="dealers/:id" element={<DealerDetailPage />} />
          <Route path="dealers/:id/edit" element={<DealerFormPage />} />
          <Route path="vehicles" element={<VehiclesPage />} />
          <Route path="vehicles/:id" element={<VehicleDetailPage />} />
          <Route path="jobs" element={<JobsPage />} />
          <Route
            path="changes"
            element={
              <Suspense fallback={<PageFallback />}>
                <ChangesPage />
              </Suspense>
            }
          />
          <Route
            path="analytics"
            element={
              <Suspense fallback={<PageFallback />}>
                <AnalyticsPage />
              </Suspense>
            }
          />
          <Route
            path="compatibility"
            element={
              <Suspense fallback={<PageFallback />}>
                <CompatibilityPage />
              </Suspense>
            }
          />
          <Route
            path="reports"
            element={
              <Suspense fallback={<PageFallback />}>
                <ReportsPage />
              </Suspense>
            }
          />
          <Route
            path="settings"
            element={
              <Suspense fallback={<PageFallback />}>
                <SettingsPage />
              </Suspense>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
        </Route>
      </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}
