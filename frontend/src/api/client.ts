import axios from 'axios';
import type { ApiError } from '../types';

/** Resolved API origin. Surfaced in the UI so operators can see what they're connected to. */
export const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

export const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
  // Send the session cookie. This replaces the API key the bundle used to
  // carry: VITE_API_KEY was compiled into the JavaScript, so anyone who could
  // load the page could read the server's key out of it. An httpOnly cookie
  // cannot be read by script at all.
  withCredentials: true,
});

/**
 * Registered by AuthProvider. Lets a 401 anywhere in the app drop the user
 * back to the login screen, rather than each page having to handle an expired
 * session itself — sessions expire on their own schedule, not the user's.
 */
let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: (() => void) | null): void {
  onUnauthorized = fn;
}

api.interceptors.response.use(
  (res) => res,
  (err) => {
    if (axios.isAxiosError(err) && err.response?.status === 401) {
      // /auth/me returning 401 is the normal "not signed in yet" answer on a
      // cold load — treating it as a session expiry would loop the redirect.
      const url = err.config?.url ?? '';
      if (!url.includes('/auth/me') && !url.includes('/auth/login')) {
        onUnauthorized?.();
      }
    }
    if (axios.isAxiosError(err) && err.response?.status === 503) {
      // eslint-disable-next-line no-console
      console.error(
        'API refused the request: the server has no API_KEY or SESSION_SECRET configured.',
      );
    }
    return Promise.reject(err);
  },
);

export function getErrorMessage(err: unknown, fallback = 'Something went wrong'): string {
  if (!axios.isAxiosError(err)) return fallback;
  const data = err.response?.data as ApiError | undefined;
  if (typeof data?.detail === 'string') return data.detail;
  if (Array.isArray(data?.detail) && data.detail[0]?.msg) return data.detail[0].msg;
  return err.message || fallback;
}
