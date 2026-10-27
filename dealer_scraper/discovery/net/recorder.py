"""Traffic recording: capture and classify requests/responses for analysis.

GraphQL requests have a distinctive body shape (query/variables/operationName).
JSON-LD and structured data are embedded in responses. This module detects
these patterns to inform candidate classification and field extraction.
"""
from __future__ import annotations

import json
from typing import Any


def is_graphql_request(method: str, url: str, body: str | bytes | None) -> bool:
    """Heuristically detect GraphQL requests by body shape and URL patterns.

    GraphQL is almost always POST to a /graphql endpoint with a JSON body
    containing 'query' and optionally 'variables' and 'operationName'.
    """
    if method.upper() != "POST":
        return False

    if "/graphql" not in url.lower():
        return False

    if not body:
        return False

    try:
        if isinstance(body, bytes):
            body = body.decode("utf-8", errors="ignore")
        data = json.loads(body)
        return isinstance(data, dict) and "query" in data
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False


def is_json_response(content_type: str | None) -> bool:
    """Check if response is JSON-shaped based on content-type."""
    if not content_type:
        return False
    return "application/json" in content_type.lower()


def extract_graphql_operation_name(body: str | bytes | None) -> str | None:
    """Extract operationName from a GraphQL request body."""
    if not body:
        return None

    try:
        if isinstance(body, bytes):
            body = body.decode("utf-8", errors="ignore")
        data = json.loads(body)
        if isinstance(data, dict):
            return data.get("operationName")
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass

    return None


def has_structured_data_markers(html: str) -> dict[str, bool]:
    """Check for common structured data markers in HTML.

    Returns a dict of presence indicators for:
    - jsonld: <script type="application/ld+json">
    - microdata: itemscope/itemtype attributes
    - next_data: __NEXT_DATA__ window variable
    - nuxt_state: __NUXT__ window variable
    - initial_state: __INITIAL_STATE__ window variable
    - apollo: __APOLLO_STATE__ window variable
    """
    markers = {
        "jsonld": 'type="application/ld+json"' in html or "application/ld+json" in html,
        "microdata": "itemscope" in html or "itemtype" in html,
        "next_data": "__NEXT_DATA__" in html,
        "nuxt_state": "__NUXT__" in html,
        "initial_state": "__INITIAL_STATE__" in html,
        "apollo": "__APOLLO_STATE__" in html,
    }
    return markers
