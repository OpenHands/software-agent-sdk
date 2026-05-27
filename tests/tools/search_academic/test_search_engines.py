"""Integration tests for academic search engines."""

import os
import sys

import pytest


_repo_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
)
sys.path.insert(0, os.path.join(_repo_root, "openhands-tools"))

from openhands.tools.search_academic.engines import (  # noqa: E402
    ArxivSearchEngine,
    ScholarSearchEngine,
    SerperSearchEngine,
)
from tests.tools.search_academic.conftest import require_env  # noqa: E402


class TestScholarSearchEngine:
    """Integration tests for Semantic Scholar search engine."""

    @pytest.mark.asyncio
    async def test_search_with_api_key(self):
        api_key = require_env("SEMANTIC_SCHOLAR_API_KEY")
        engine = ScholarSearchEngine(api_key=api_key, timeout=30)
        response = await engine.search("machine learning", max_results=5)

        assert response.engine == "scholar"
        assert response.total_found > 0
        assert len(response.results) > 0

    @pytest.mark.asyncio
    async def test_search_result_structure(self):
        """Search results must have title, url, snippet (abstract), and pdf_url."""
        api_key = require_env("SEMANTIC_SCHOLAR_API_KEY")
        engine = ScholarSearchEngine(api_key=api_key, timeout=30)
        response = await engine.search("transformer attention", max_results=3)

        assert response.total_found > 0
        result = response.results[0]
        assert result.title
        assert result.url
        assert result.source == "scholar"
        assert result.snippet and len(result.snippet) > 50, (
            f"Expected snippet to include abstract, got: {result.snippet!r}"
        )
        assert hasattr(result, "pdf_url")


class TestArxivSearchEngine:
    """Integration tests for arXiv search engine."""

    @pytest.mark.asyncio
    async def test_search_returns_results(self):
        engine = ArxivSearchEngine(timeout=30)
        response = await engine.search("machine learning", max_results=5)

        assert response.engine == "arxiv"
        assert response.total_found > 0
        assert len(response.results) > 0

    @pytest.mark.asyncio
    async def test_search_result_structure(self):
        """Search results must have title, url (abs page), snippet, and pdf_url."""
        engine = ArxivSearchEngine(timeout=30)
        response = await engine.search("neural networks", max_results=3)

        assert response.total_found > 0
        result = response.results[0]
        assert result.title
        assert result.source == "arxiv"
        assert result.snippet, "Expected non-empty snippet (arXiv summary)"
        assert result.url and "arxiv.org/abs/" in result.url, (
            f"Expected abs page URL, got: {result.url!r}"
        )
        assert result.pdf_url and "arxiv.org/pdf/" in result.pdf_url, (
            f"Expected PDF URL, got: {result.pdf_url!r}"
        )


class TestSerperSearchEngine:
    """Integration tests for Serper (Google) search engine."""

    @pytest.mark.asyncio
    async def test_search_with_api_key(self):
        api_key = require_env("SERPER_API_KEY")
        engine = SerperSearchEngine(api_key=api_key, timeout=30)
        response = await engine.search("machine learning", max_results=5)

        assert response.engine == "serper"
        assert response.total_found > 0
        assert len(response.results) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
