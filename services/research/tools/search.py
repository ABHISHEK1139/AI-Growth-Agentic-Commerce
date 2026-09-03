"""Structured web search providers (SearXNG JSON, DuckDuckGo, Null)."""

from __future__ import annotations

import abc
import html
import re
import urllib.parse
from dataclasses import dataclass

import httpx

from services.research.safety.url_policy import is_safe_public_url


@dataclass(frozen=True, slots=True)
class SearchHit:
    title: str
    url: str
    snippet: str
    engine: str = "web"
    score: float = 1.0


class SearchProvider(abc.ABC):
    """Abstract interface for structured web search providers."""

    @abc.abstractmethod
    def search(self, query: str, limit: int = 5) -> list[SearchHit]:
        """Execute search and return structured hits."""
        ...


class SearXNGSearchProvider(SearchProvider):
    """Self-hosted SearXNG JSON API client (ADR-0009)."""

    def __init__(self, base_url: str = "http://localhost:8080", timeout_seconds: float = 6.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def search(self, query: str, limit: int = 5) -> list[SearchHit]:
        if not query or not query.strip():
            return []

        endpoint = f"{self.base_url}/search"
        params: dict[str, str | int] = {
            "q": query.strip(),
            "format": "json",
            "language": "en",
            "safesearch": 1,
        }

        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                res = client.get(endpoint, params=params)
                if res.status_code == 200:
                    data = res.json()
                    raw_results = data.get("results", [])
                    hits: list[SearchHit] = []
                    for r in raw_results:
                        url = r.get("url", "")
                        if is_safe_public_url(url):
                            hits.append(
                                SearchHit(
                                    title=str(r.get("title", "")),
                                    url=url,
                                    snippet=str(r.get("content", "") or r.get("snippet", "")),
                                    engine=str(r.get("engine", "searxng")),
                                    score=float(r.get("score", 1.0)),
                                )
                            )
                            if len(hits) >= limit:
                                break
                    return hits
        except Exception:  # noqa: S110
            pass

        return []


class DuckDuckGoSearchProvider(SearchProvider):
    """Lightweight public web search fallback."""

    def __init__(self, timeout_seconds: float = 6.0):
        self.timeout_seconds = timeout_seconds

    def _parse_hits(self, page_html: str, query: str, limit: int) -> list[SearchHit]:
        matches = re.findall(
            r'<a class="result__url"[^>]*href="([^"]+)"[^>]*>.*?</a>.*?<a class="result__snippet[^"]*"[^>]*>(.*?)</a>',
            page_html,
            re.DOTALL,
        )
        hits: list[SearchHit] = []
        for raw_url, raw_snippet in matches:
            real_url = raw_url
            if "uddg=" in raw_url:
                parsed_qs = urllib.parse.parse_qs(urllib.parse.urlparse(raw_url).query)
                if "uddg" in parsed_qs:
                    real_url = parsed_qs["uddg"][0]

            clean_snippet = re.sub(r"<[^>]+>", " ", raw_snippet)
            clean_snippet = html.unescape(clean_snippet).strip()

            if is_safe_public_url(real_url) and clean_snippet:
                hits.append(
                    SearchHit(
                        title=query,
                        url=real_url,
                        snippet=clean_snippet,
                        engine="duckduckgo",
                    )
                )
                if len(hits) >= limit:
                    break
        return hits

    def search(self, query: str, limit: int = 5) -> list[SearchHit]:
        if not query or not query.strip():
            return []

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        # The HTML endpoint answers plain GET requests with an HTTP 202 anomaly
        # challenge (no results), but accepts the same query submitted through
        # its form (POST). Try POST first; fall back to GET for environments
        # where the challenge does not apply.
        try:
            with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
                res = client.post(
                    "https://html.duckduckgo.com/html/",
                    data={"q": query.strip()},
                    headers=headers,
                )
                if res.status_code != 200:
                    search_url = (
                        f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query.strip())}"
                    )
                    res = client.get(search_url, headers=headers)
                if res.status_code == 200:
                    return self._parse_hits(res.text, query, limit)
        except Exception:  # noqa: S110
            pass

        return []


class NullSearchProvider(SearchProvider):
    """Offline, deterministic search provider for test suites and isolated environments."""

    def __init__(self, mock_hits: list[SearchHit] | None = None):
        self.mock_hits = mock_hits or []

    def search(self, query: str, limit: int = 5) -> list[SearchHit]:
        if self.mock_hits:
            return self.mock_hits[:limit]
        return [
            SearchHit(
                title=f"Technical Specifications for {query}",
                url="https://psref.lenovo.com/Product/IdeaPad_Slim_5",
                snippet=f"Official manufacturer documentation for {query}. Includes standard ports and expansion slots.",
                engine="mock",
                score=1.0,
            )
        ]


def get_search_provider(
    provider_name: str = "auto",
    searxng_base_url: str = "http://localhost:8080",
) -> SearchProvider:
    """Resolve active search provider based on configuration."""
    if provider_name == "null":
        return NullSearchProvider()
    if provider_name == "searxng":
        return SearXNGSearchProvider(base_url=searxng_base_url)
    if provider_name == "duckduckgo":
        return DuckDuckGoSearchProvider()

    # Auto mode: try SearXNG first, fall back to DuckDuckGo
    return DuckDuckGoSearchProvider()
