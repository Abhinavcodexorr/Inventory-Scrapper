import { createContext, useContext } from 'react';

interface RunScraperContextValue {
  /** Opens the confirmation dialog owned by the app shell. */
  open: () => void;
  /** True while sync dispatch is in flight. */
  isRunning: boolean;
}

export const RunScraperContext = createContext<RunScraperContextValue>({
  open: () => undefined,
  isRunning: false,
});

/**
 * Lets any page trigger the global "Run Scraper" flow without owning a second
 * confirmation dialog — there is exactly one, in the shell.
 */
export function useRunScraperAction(): RunScraperContextValue {
  return useContext(RunScraperContext);
}
