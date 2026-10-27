/**
 * Repoint a scrape_config copied from another dealer at this dealer's own site.
 *
 * Reusing a proven config is the whole point of platform templates, but a
 * verbatim copy keeps the template dealer's list_url — so the new dealership
 * would scrape the *other* dealer's inventory and store it under its own name.
 * That failure is silent and entirely plausible-looking once two dealers share
 * a platform, so the URLs are rewritten rather than left for a human to catch.
 *
 * Mirrors `_retarget_config` in api/routers/dealers.py: every string in the
 * config is considered, nested ones included (next_build_id_url, detail.url),
 * and a vendor-hosted feed named after the dealer (cardora.zopsoftware.com)
 * moves with it (newcastleauto.zopsoftware.com). Paths and query strings are
 * the platform-shaped part worth reusing, so they are kept verbatim.
 */

function bareHost(url: URL): string {
  const host = url.hostname.toLowerCase();
  return host.startsWith('www.') ? host.slice(4) : host;
}

/** First label of a host without www.: www.cardora.ca -> cardora. */
function siteLabel(url: URL): string {
  return bareHost(url).split('.')[0];
}

function parseAbsolute(value: string): URL | null {
  if (!value.includes('://')) return null;
  try {
    return new URL(value);
  } catch {
    return null;
  }
}

/** Rebuild with a new origin, keeping `{page}`-style templates unescaped. */
function withOrigin(value: string, origin: string): string {
  const rest = value.slice(value.indexOf('://') + 3);
  const slash = rest.search(/[/?#]/);
  return slash === -1 ? origin : origin + rest.slice(slash);
}

function retargetValue(value: unknown, from: URL, to: URL): unknown {
  if (Array.isArray(value)) return value.map((v) => retargetValue(v, from, to));
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([k, v]) => [k, retargetValue(v, from, to)]),
    );
  }
  if (typeof value !== 'string' || !value) return value;
  const url = parseAbsolute(value);
  if (!url) return value;
  if (bareHost(url) === bareHost(from)) {
    return withOrigin(value, `${to.protocol}//${to.host}`);
  }
  const src = siteLabel(from);
  const dst = siteLabel(to);
  const host = url.host.toLowerCase();
  if (src && dst && src !== dst && host.startsWith(`${src}.`)) {
    return withOrigin(value, `${url.protocol}//${dst}${host.slice(src.length)}`);
  }
  return value;
}

export function retargetConfig(
  config: Record<string, unknown>,
  fromBaseUrl: string,
  toBaseUrl: string,
): Record<string, unknown> {
  let from: URL;
  let to: URL;
  try {
    from = new URL(fromBaseUrl);
    to = new URL(toBaseUrl);
  } catch {
    return { ...config };
  }
  if (bareHost(from) === bareHost(to)) return { ...config };
  return retargetValue(config, from, to) as Record<string, unknown>;
}

/**
 * True when any URL in the config still points at another dealer's site.
 * A vendor feed carrying this dealer's own name (newcastleauto.zopsoftware.com)
 * belongs to this dealer, as does a shared third-party host that names no
 * dealer at all; only a host named after a different site is flagged.
 */
export function hasForeignUrls(
  config: Record<string, unknown>,
  baseUrl: string,
  templateBaseUrl?: string | null,
): boolean {
  let target: URL;
  try {
    target = new URL(baseUrl);
  } catch {
    return false;
  }
  let template: URL | null = null;
  try {
    template = templateBaseUrl ? new URL(templateBaseUrl) : null;
  } catch {
    template = null;
  }
  const check = (value: unknown): boolean => {
    if (Array.isArray(value)) return value.some(check);
    if (value && typeof value === 'object') return Object.values(value).some(check);
    if (typeof value !== 'string') return false;
    const url = parseAbsolute(value);
    if (!url || bareHost(url) === bareHost(target)) return false;
    if (!template) return true;
    return bareHost(url) === bareHost(template) || url.host.toLowerCase().startsWith(`${siteLabel(template)}.`);
  };
  return check(config);
}
