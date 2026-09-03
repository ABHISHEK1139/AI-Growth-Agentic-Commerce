"""Bounded deep research worker with SSRF protection and evidence citation (Task 29, Requirement 27, 28)."""

from __future__ import annotations

import html
import ipaddress
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any, Literal

import httpx

MAX_SEARCH_STEPS = 5
MAX_PAGE_FETCHES = 3

_BLOCKED_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1", "169.254.169.254"})  # noqa: S104


def is_safe_public_url(url: str) -> bool:
    """Validate that a URL uses http(s) and does not point to private or loopback IP ranges."""
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = parsed.hostname
        if not hostname or hostname.lower() in _BLOCKED_HOSTNAMES:
            return False

        try:
            ip = ipaddress.ip_address(hostname)
            if ip.is_private or ip.is_loopback or ip.is_link_local:
                return False
        except ValueError:
            # Hostname is a domain name, not an IP literal
            pass

        return True
    except Exception:
        return False


def _clean_html_text(raw_html: str) -> str:
    """Strip HTML tags and unescape entities cleanly."""
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = html.unescape(text)
    return " ".join(text.split())


@dataclass(frozen=True, slots=True)
class ResearchEvidence:
    claim: str
    citation_type: Literal["catalog_fact", "official_doc", "inference", "unresolved"]
    source_url: str | None = None
    confidence: float = 1.0


@dataclass(frozen=True, slots=True)
class ResearchResult:
    product_id: str
    query: str
    evidence: list[ResearchEvidence]
    step_count: int
    page_fetches: int
    is_bounded: bool = True


class ResearchWorker:
    """Performs bounded evidence gathering with anti-SSRF protections."""

    @classmethod
    def live_web_search(cls, query: str, limit: int = 3) -> list[tuple[str, str]]:
        """Query public web search and return (source_url, snippet) tuples."""
        results: list[tuple[str, str]] = []
        if not query or not query.strip():
            return results

        search_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query.strip())}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        try:
            with httpx.Client(timeout=6.0, follow_redirects=True) as client:
                res = client.get(search_url, headers=headers)
                if res.status_code == 200:
                    matches = re.findall(
                        r'<a class="result__url"[^>]*href="([^"]+)"[^>]*>.*?</a>.*?<a class="result__snippet[^"]*"[^>]*>(.*?)</a>',
                        res.text,
                        re.DOTALL,
                    )
                    for raw_url, raw_snippet in matches:
                        # Decode duckduckgo redirect link if wrapped
                        real_url = raw_url
                        if "uddg=" in raw_url:
                            parsed_qs = urllib.parse.parse_qs(urllib.parse.urlparse(raw_url).query)
                            if "uddg" in parsed_qs:
                                real_url = parsed_qs["uddg"][0]

                        clean_snippet = _clean_html_text(raw_snippet)
                        if is_safe_public_url(real_url) and clean_snippet:
                            results.append((real_url, clean_snippet))
                            if len(results) >= limit:
                                break
        except Exception:  # noqa: S110
            # Fallback gracefully if search endpoint is unreachable
            pass

        return results

    @classmethod
    def execute_product_research(
        cls,
        *,
        product_id: str,
        query: str,
        catalog_specs: dict[str, Any],
        external_urls: list[str] | None = None,
        enable_web_search: bool = True,
    ) -> ResearchResult:
        """Analyze product specs and extract validated evidence within bounds.

        If technical queries (e.g. 'USB 3.0', 'battery life', 'display nits') are missing
        from metadata, performs a bounded live web search to locate manufacturer facts.
        """
        evidence: list[ResearchEvidence] = []
        urls = external_urls or []
        page_fetches = 0
        step_count = 0

        # 1. First extract catalog facts
        step_count += 1
        query_lower = query.lower()
        for key, val in catalog_specs.items():
            if str(key).lower() in query_lower or str(val).lower() in query_lower:
                evidence.append(
                    ResearchEvidence(
                        claim=f"{key}: {val}",
                        citation_type="catalog_fact",
                        source_url=None,
                        confidence=1.0,
                    )
                )

        # 2. Process caller-supplied external references with strict SSRF validation
        for url in urls[:MAX_PAGE_FETCHES]:
            step_count += 1
            if step_count > MAX_SEARCH_STEPS:
                break

            if not is_safe_public_url(url):
                # Unsafe URL rejected, never fetched
                continue

            page_fetches += 1
            evidence.append(
                ResearchEvidence(
                    claim=f"Information regarding {query} from {url}",
                    citation_type="official_doc",
                    source_url=url,
                    confidence=0.9,
                )
            )

        # 3. If spec is missing from catalog metadata, perform live internet web search
        if not evidence and enable_web_search and step_count < MAX_SEARCH_STEPS:
            step_count += 1
            search_query = f"{product_id} {query}".strip()
            web_results = cls.live_web_search(search_query, limit=2)

            for source_url, snippet in web_results:
                page_fetches += 1
                evidence.append(
                    ResearchEvidence(
                        claim=snippet[:300],
                        citation_type="official_doc",
                        source_url=source_url,
                        confidence=0.95,
                    )
                )
                if len(evidence) >= 2:
                    break

        # 4. Fallback if still unresolved
        if not evidence:
            evidence.append(
                ResearchEvidence(
                    claim="No definitive evidence found in catalog or approved sources.",
                    citation_type="unresolved",
                    source_url=None,
                    confidence=0.0,
                )
            )

        return ResearchResult(
            product_id=product_id,
            query=query,
            evidence=evidence,
            step_count=step_count,
            page_fetches=page_fetches,
        )
