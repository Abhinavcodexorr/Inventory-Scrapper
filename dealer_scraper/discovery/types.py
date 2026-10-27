"""Typed scrape_config builders.

These serialize to EXACTLY the dict keys dealer_scraper/adapters/json_api.py and
dealer_scraper/adapters/browser.py already consume (see their module
docstrings) — discovery only ever hands the adapters a plain dict, so a typed
model here buys validation and IDE support without touching the adapter layer.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class JsonApiConfig(BaseModel):
    endpoint: str | None = None
    endpoints: list[str] | None = None
    page_size: int = 25
    start_page: int = 1
    page_step: int | None = None
    list_path: str = ""
    total_path: str | None = None
    field_map: dict[str, str]
    headers: dict[str, str] = Field(default_factory=dict)
    max_pages: int | None = None
    method: str | None = None
    body: dict | None = None
    defaults: dict | None = None
    url_base: str | None = None
    next_build_id_url: str | None = None
    detail: dict | None = None
    delay_seconds: float | None = None

    @model_validator(mode="after")
    def _require_endpoint(self) -> "JsonApiConfig":
        if not self.endpoint and not self.endpoints:
            raise ValueError("JsonApiConfig needs endpoint or endpoints")
        return self

    def to_dict(self) -> dict:
        return self.model_dump(exclude_none=True)


class BrowserConfig(BaseModel):
    list_url: str
    base_url: str | None = None
    card_anchor: str | None = None
    card_selector: str | None = None
    price_label: str | None = None
    wait_selector: str | None = None
    id_regex: str | None = None
    paginate: str = "scroll"
    page_url: str | None = None
    next_selector: str | None = None
    scroll_rounds: int | None = None
    max_pages: int | None = None
    headless: bool = True
    nav_timeout: int | None = None
    settle_ms: int | None = None
    total_selector: str | None = None
    extract: str = "dom"

    def to_dict(self) -> dict:
        return self.model_dump(exclude_none=True)
