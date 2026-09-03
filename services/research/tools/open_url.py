"""Bounded, SSRF-protected HTTP page fetcher."""

from __future__ import annotations

import httpx

from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from services.research.safety.url_policy import is_safe_public_url


class PageFetcher:
    """Safely retrieves public web pages within size and time bounds."""

    @classmethod
    def fetch_page(
        cls,
        url: str,
        *,
        timeout_seconds: float = 8.0,
        max_bytes: int = 1_000_000,
    ) -> str:
        """Fetch raw HTML/text from a public URL with strict anti-SSRF enforcement."""
        if not is_safe_public_url(url):
            raise DomainError(
                f"URL '{url}' violates anti-SSRF policy: access to local, internal, or cloud metadata addresses is blocked.",
                code=ErrorCode.FORBIDDEN,
            )

        headers = {
            "User-Agent": "AgentPayResearchBot/1.0 (Commercial AI Assistant; safe research fetcher)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

        try:
            with httpx.Client(
                timeout=timeout_seconds,
                follow_redirects=True,
                max_redirects=3,
            ) as client:
                res = client.get(url, headers=headers)
                if res.status_code != 200:
                    # The registry has no BAD_GATEWAY row; an upstream that answers
                    # with anything but 200 is an unavailable dependency here, and
                    # naming a code that does not exist raised AttributeError on the
                    # one path this branch was written for.
                    raise DomainError(
                        f"External page returned HTTP status {res.status_code}",
                        code=ErrorCode.SERVICE_UNAVAILABLE,
                    )

                content_type = res.headers.get("content-type", "").lower()
                if (
                    "text" not in content_type
                    and "json" not in content_type
                    and "xml" not in content_type
                ):
                    raise DomainError(
                        f"Unsupported content type '{content_type}' for research page",
                        code=ErrorCode.VALIDATION_ERROR,
                    )

                return res.text[:max_bytes]
        except httpx.TimeoutException as exc:
            raise DomainError(
                "External page fetch timed out", code=ErrorCode.GATEWAY_TIMEOUT
            ) from exc
        except DomainError:
            raise
        except Exception as exc:
            raise DomainError(
                f"Failed to fetch external page: {exc}", code=ErrorCode.SERVICE_UNAVAILABLE
            ) from exc
