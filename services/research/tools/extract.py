"""Clean content extractor for research pages."""

from __future__ import annotations

import re

from services.research.safety.content_sanitizer import sanitize_evidence_text


class ContentExtractor:
    """Extracts informative factual snippets from raw HTML documents."""

    @classmethod
    def extract_relevant_snippets(
        cls,
        raw_html: str,
        query: str,
        max_snippets: int = 3,
        snippet_max_chars: int = 400,
    ) -> list[str]:
        """Extract main body text and identify snippets most relevant to query keywords."""
        sanitized = sanitize_evidence_text(raw_html, max_chars=100_000)
        if not sanitized:
            return []

        # Split into coherent paragraphs or spec lines
        blocks = [b.strip() for b in re.split(r"\n{2,}|\.\s+", sanitized) if len(b.strip()) > 20]

        query_terms = [t.lower() for t in re.findall(r"\w+", query) if len(t) > 2]
        if not query_terms:
            return blocks[:max_snippets]

        scored_blocks: list[tuple[int, str]] = []
        for block in blocks:
            lower_block = block.lower()
            score = sum(1 for term in query_terms if term in lower_block)
            if score > 0:
                scored_blocks.append((score, block[:snippet_max_chars]))

        scored_blocks.sort(key=lambda x: x[0], reverse=True)
        if scored_blocks:
            return [b[1] for b in scored_blocks[:max_snippets]]

        # Fallback to initial paragraphs if no keyword matched
        return [b[:snippet_max_chars] for b in blocks[:max_snippets]]
