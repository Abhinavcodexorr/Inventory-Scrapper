"""Pure snapshot -> findings analyzers. No Playwright here — every function
takes already-captured data (link lists, bounding boxes, ...) so it is
testable without a browser and reusable against non-browser sources
(sitemap-derived URL lists, cached page snapshots).
"""
