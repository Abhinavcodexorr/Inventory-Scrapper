import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { api, setUnauthorizedHandler } from '../api/client';

export interface AuthUser {
  id: number;
  email: string;
  full_name?: string | null;
}

interface AuthState {
  user: AuthUser | null;
  /** True until the first /auth/me has answered. Routes must wait for this:
   *  rendering the login page while the check is still in flight makes a
   *  signed-in operator see a login form flash on every page load. */
  loading: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);

  // Ask the server who we are. The session lives in an httpOnly cookie the
  // JavaScript cannot inspect, so this call is the only way to know.
  useEffect(() => {
    let cancelled = false;
    api
      .get<AuthUser>('/api/auth/me')
      .then((res) => {
        if (!cancelled) setUser(res.data);
      })
      .catch(() => {
        if (!cancelled) setUser(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Any 401 from any request means the session went away mid-use — usually
  // expiry, occasionally the account being disabled while someone is working.
  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null));
    return () => setUnauthorizedHandler(null);
  }, []);

  const signIn = useCallback(async (email: string, password: string) => {
    const res = await api.post<AuthUser>('/api/auth/login', { email, password });
    setUser(res.data);
  }, []);

  const signOut = useCallback(async () => {
    try {
      await api.post('/api/auth/logout');
    } finally {
      // Clear locally even if the call failed — the user asked to leave, and
      // a network error should not strand them in a session they've abandoned.
      setUser(null);
    }
  }, []);

  const value = useMemo(
    () => ({ user, loading, signIn, signOut }),
    [user, loading, signIn, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>');
  return ctx;
}
