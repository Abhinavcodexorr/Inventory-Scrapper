"""Playwright session lifecycle: launch once, create stealth contexts under a
given retry profile (so a detected anti-bot block can escalate to a fresh
identity without relaunching the whole browser process), and capture the raw
link/bounding-box data the pure-Python analyzers turn into evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ...adapters.base import chromium_launch_args, configured_playwright_proxy
from ...config import settings
from .antibot import profile_for_attempt

_STEALTH_INIT_JS = "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"

# All internal link paths + anchor text on the current page — the raw input
# to analyzers.dom_repetition's open-ended shape clustering. Deliberately
# NOT filtered by any closed segment list (that's the whole Phase 2 point).
_LINKS_JS = r"""
() => {
  const out = [];
  const seen = new Set();
  // Is this anchor part of the site's furniture (nav / header / footer /
  // sidebar) rather than the page's own content? Dealer sites carry SEO menus
  // full of links like "/used-sedans-under-5000" — hyphenated, digit-bearing,
  // and therefore indistinguishable from a vehicle slug by shape alone. They
  // repeat identically on EVERY page, so a shape cluster built over them looks
  // exactly like a repeating record type and wins by sheer count. Marking them
  // at capture time is the only place we can still see the DOM position that
  // tells them apart.
  const isChrome = (el) => {
    for (let n = el; n && n !== document.body; n = n.parentElement) {
      const tag = (n.tagName || '').toLowerCase();
      if (tag === 'nav' || tag === 'header' || tag === 'footer' || tag === 'aside') return true;
      const role = (n.getAttribute && n.getAttribute('role') || '').toLowerCase();
      if (role === 'navigation' || role === 'banner' || role === 'contentinfo') return true;
      const cls = (n.className && n.className.baseVal !== undefined
                   ? n.className.baseVal : n.className) || '';
      if (typeof cls === 'string' && /(^|[\s_-])(nav|navbar|menu|header|footer|breadcrumb|sidebar)([\s_-]|$)/i.test(cls)) return true;
    }
    return false;
  };
  for (const a of document.querySelectorAll('a[href]')) {
    if (out.length >= 2000) break;
    const href = a.getAttribute('href') || '';
    if (!href || href.startsWith('#') || href.startsWith('mailto:') || href.startsWith('tel:')) continue;
    let path;
    try { path = new URL(href, location.origin).pathname; } catch (e) { continue; }
    if (seen.has(path)) continue;
    seen.add(path);
    out.push({ path, text: (a.textContent || '').trim().slice(0, 60), chrome: isChrome(a) });
  }
  return out;
}
"""

# Bounding boxes of the "card" ancestor for a given set of link paths — climbs
# ancestors the same way adapters/browser.py's _COLLECT_JS does (stop once
# enough text has accumulated to look like a real card, not just the anchor).
_CARD_BOXES_JS = r"""
(paths) => {
  const wanted = new Set(paths);
  const boxes = [];
  for (const a of document.querySelectorAll('a[href]')) {
    if (boxes.length >= 200) break;
    const href = a.getAttribute('href') || '';
    let path;
    try { path = new URL(href, location.origin).pathname; } catch (e) { continue; }
    if (!wanted.has(path)) continue;
    let node = a;
    for (let i = 0; i < 6 && node.parentElement; i++) {
      if ((node.textContent || '').trim().length > 40) break;
      node = node.parentElement;
    }
    const r = node.getBoundingClientRect();
    if (r.width > 0 && r.height > 0) boxes.push({ x: r.x, y: r.y, width: r.width, height: r.height });
  }
  return boxes;
}
"""


@dataclass
class PageSnapshot:
    url: str
    links: list[dict] = field(default_factory=list)   # [{"path": str, "text": str}]


def capture_links(page) -> list[dict]:
    """Never raises — an evaluate failure just means an empty snapshot."""
    try:
        return page.evaluate(_LINKS_JS)
    except Exception:
        return []


def capture_card_boxes(page, paths: list[str]) -> list[dict]:
    try:
        return page.evaluate(_CARD_BOXES_JS, paths)
    except Exception:
        return []


class SessionManager:
    """Owns one Playwright browser instance and creates stealth contexts
    under a given retry profile, so a detected anti-bot block can retry
    under an escalated identity (new UA/viewport) without paying the cost of
    relaunching the whole browser process.
    """

    def __init__(self, playwright, *, headless: bool = True) -> None:
        self._playwright = playwright
        launch_args = {"headless": headless, "args": chromium_launch_args()}
        proxy = configured_playwright_proxy()
        if proxy:
            launch_args["proxy"] = proxy
        self.browser = playwright.chromium.launch(**launch_args)
        self._context = None
        self._next_attempt = 0

    def new_context(self, attempt: int | None = None):
        """Close any existing context and open a fresh one under the retry
        profile for `attempt` (defaults to escalating by one each call —
        attempt 0 is the same profile discovery always used, so the common,
        non-blocked case sees no behavior change)."""
        if self._context is not None:
            try:
                self._context.close()
            except Exception:
                pass
        if attempt is None:
            attempt = self._next_attempt
        self._next_attempt = attempt + 1

        profile = profile_for_attempt(attempt)
        self._context = self.browser.new_context(
            user_agent=profile.user_agent, viewport=profile.viewport,
            locale=profile.locale, timezone_id=profile.timezone_id,
        )
        self._context.add_init_script(_STEALTH_INIT_JS)
        return self._context

    def new_page(self):
        if self._context is None:
            self.new_context(attempt=0)
        return self._context.new_page()

    def close(self) -> None:
        try:
            if self._context is not None:
                self._context.close()
        except Exception:
            pass
        try:
            self.browser.close()
        except Exception:
            pass
