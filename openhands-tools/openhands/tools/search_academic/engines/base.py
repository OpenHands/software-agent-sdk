"""Base search engine implementation for academic paper search."""

import logging
from abc import ABC, abstractmethod
from typing import Any, ClassVar

import httpx

from openhands.tools.search_academic.models import SearchResponse, SearchResult


logger = logging.getLogger(__name__)


class BaseSearchEngine(ABC):
    """Abstract base class for search engines."""

    def __init__(
        self,
        api_key: str | None = None,
        timeout: int = 30,
    ) -> None:
        """Initialize search engine.

        Args:
            api_key: Optional API key for the search engine
            timeout: Request timeout in seconds
        """
        self.api_key = api_key
        self.timeout = timeout

    @abstractmethod
    async def search(self, query: str, max_results: int = 10) -> SearchResponse:
        """Search for academic papers.

        Args:
            query: Search query string
            max_results: Maximum number of results to return

        Returns:
            SearchResponse with results
        """
        pass

    @staticmethod
    @abstractmethod
    def parse_results(raw_response: str | dict[str, Any]) -> list[SearchResult]:
        """Parse raw API response to SearchResult objects.

        Args:
            raw_response: Raw response from API (XML string or JSON dict)

        Returns:
            List of SearchResult objects
        """
        pass

    async def _fetch_with_retry(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        max_retries: int = 3,
    ) -> dict[str, Any]:
        """Fetch data from URL with retry logic.

        Args:
            url: URL to fetch
            headers: Optional HTTP headers
            params: Optional query parameters
            max_retries: Maximum number of retries

        Returns:
            Parsed JSON response

        Raises:
            Exception: If all retries fail
        """
        last_exception = None

        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.get(url, headers=headers, params=params)
                    response.raise_for_status()
                    return response.json()  # type: ignore

            except Exception as e:
                last_exception = e
                logger.warning(
                    f"Fetch attempt {attempt + 1} failed: {e}. Retrying..."
                    if attempt < max_retries - 1
                    else "Giving up."
                )

        if last_exception:
            raise last_exception
        raise RuntimeError("Failed to fetch data after retries")


class SerperSearchEngine(BaseSearchEngine):
    """Google Search via Serper API."""

    def __init__(
        self,
        api_key: str | None = None,
        timeout: int = 30,
    ) -> None:
        """Initialize Serper search engine.

        Note: API key should be set via SERPER_API_KEY environment variable
        """
        super().__init__(api_key=api_key, timeout=timeout)
        self.engine_name = "serper"
        self.api_endpoint = "https://google.serper.dev/search"

    async def search(self, query: str, max_results: int = 10) -> SearchResponse:
        """Search using Serper API.

        Args:
            query: Search query
            max_results: Maximum results to return

        Returns:
            SearchResponse with results
        """
        if not self.api_key:
            logger.warning(
                f"{self.engine_name} API key not configured. "
                "Set SERPER_API_KEY environment variable."
            )
            return SearchResponse(
                query=query,
                results=[],
                total_found=0,
                engine=self.engine_name,
                execution_time=0.0,
            )

        try:
            headers = {"X-API-KEY": self.api_key}
            params = {"q": query, "num": max_results}

            response_data: dict[str, Any] = await self._fetch_with_retry(
                self.api_endpoint,
                headers=headers,
                params=params,
            )

            results = self.parse_results(response_data)

            return SearchResponse(
                query=query,
                results=results[:max_results],
                total_found=len(results),
                engine=self.engine_name,
                execution_time=0.0,
            )
        except Exception as e:
            logger.error(f"Serper search failed: {e}")
            return SearchResponse(
                query=query,
                results=[],
                total_found=0,
                engine=self.engine_name,
                execution_time=0.0,
            )

    @staticmethod
    def parse_results(raw_response: str | dict[str, Any]) -> list[SearchResult]:
        """Parse Serper API response.

        Args:
            raw_response: Raw response from Serper API

        Returns:
            List of SearchResult objects
        """
        if not isinstance(raw_response, dict):
            return []
        results = []
        for item in raw_response.get("organic", []):
            result = SearchResult(
                title=item.get("title", ""),
                url=item.get("link", ""),
                source="serper",
                snippet=item.get("snippet", ""),
                relevance_score=0.8,
            )
            results.append(result)
        return results


class ScholarSearchEngine(BaseSearchEngine):
    """Semantic Scholar search engine (requires API key)."""

    def __init__(
        self,
        api_key: str | None = None,
        timeout: int = 30,
    ) -> None:
        """Initialize Semantic Scholar search engine.

        Args:
            api_key: API key for Semantic Scholar.
                If not provided, searches will return empty.
            timeout: Request timeout in seconds
        """
        super().__init__(api_key=api_key, timeout=timeout)
        self.engine_name = "scholar"
        self.api_endpoint = "https://api.semanticscholar.org/graph/v1/paper/search"

    async def search(self, query: str, max_results: int = 10) -> SearchResponse:
        """Search using Semantic Scholar API.

        Args:
            query: Search query
            max_results: Maximum results to return

        Returns:
            SearchResponse with results
        """
        if not self.api_key:
            logger.warning(
                f"{self.engine_name} API key not configured. "
                "Set SEMANTIC_SCHOLAR_API_KEY environment variable."
            )
            return SearchResponse(
                query=query,
                results=[],
                total_found=0,
                engine=self.engine_name,
                execution_time=0.0,
            )

        try:
            params = {
                "query": query,
                "limit": max_results,
                "fields": (
                    "paperId,title,url,authors,publicationDate,"
                    "abstract,openAccessPdf,isOpenAccess"
                ),
            }

            headers = {"x-api-key": self.api_key}
            response_data: dict[str, Any] = await self._fetch_with_retry(
                self.api_endpoint,
                params=params,
                headers=headers,
            )

            results = self.parse_results(response_data)

            return SearchResponse(
                query=query,
                results=results[:max_results],
                total_found=len(results),
                engine=self.engine_name,
                execution_time=0.0,
            )
        except Exception as e:
            logger.error(f"Semantic Scholar search failed: {e}")
            return SearchResponse(
                query=query,
                results=[],
                total_found=0,
                engine=self.engine_name,
                execution_time=0.0,
            )

    @staticmethod
    def parse_results(raw_response: str | dict[str, Any]) -> list[SearchResult]:
        """Parse Semantic Scholar API response.

        Args:
            raw_response: Raw response from Semantic Scholar API

        Returns:
            List of SearchResult objects
        """
        if not isinstance(raw_response, dict):
            return []
        results = []
        for item in raw_response.get("data", []):
            authors = [a.get("name", "") for a in item.get("authors", [])]
            open_access = item.get("openAccessPdf") or {}
            result = SearchResult(
                title=item.get("title", ""),
                url=f"https://semanticscholar.org/paper/{item.get('paperId', '')}",
                source="scholar",
                snippet=item.get("abstract"),
                authors=authors,
                pdf_url=open_access.get("url"),
                relevance_score=0.85,
            )
            results.append(result)
        return results


class ArxivSearchEngine(BaseSearchEngine):
    """arXiv search engine (free)."""

    NS: ClassVar[dict[str, str]] = {
        "atom": "http://www.w3.org/2005/Atom",
        "arxiv": "http://arxiv.org/schemas/atom",
    }

    def __init__(
        self,
        timeout: int = 30,
    ) -> None:
        """Initialize arXiv search engine."""
        super().__init__(api_key=None, timeout=timeout)
        self.engine_name = "arxiv"
        self.api_endpoint = "http://export.arxiv.org/api/query"

    async def search(self, query: str, max_results: int = 10) -> SearchResponse:
        """Search using arXiv API.

        Args:
            query: Search query
            max_results: Maximum results to return

        Returns:
            SearchResponse with results
        """
        try:
            params = {
                "search_query": f"all:{query}",
                "start": 0,
                "max_results": max_results,
                "sortBy": "relevance",
            }

            # Get raw XML response
            async with httpx.AsyncClient(
                timeout=self.timeout, follow_redirects=True
            ) as client:
                response = await client.get(self.api_endpoint, params=params)
                response.raise_for_status()
                xml_text = response.text

            results = self.parse_results(xml_text)

            return SearchResponse(
                query=query,
                results=results[:max_results],
                total_found=len(results),
                engine=self.engine_name,
                execution_time=0.0,
            )
        except Exception as e:
            logger.error(f"arXiv search failed: {e}")
            return SearchResponse(
                query=query,
                results=[],
                total_found=0,
                engine=self.engine_name,
                execution_time=0.0,
            )

    @staticmethod
    def parse_results(raw_response: str | dict[str, Any]) -> list[SearchResult]:
        """Parse arXiv API XML response.

        Args:
            raw_response: Raw XML response from arXiv API

        Returns:
            List of SearchResult objects
        """
        if not isinstance(raw_response, str):
            return []
        import xml.etree.ElementTree as ET
        from datetime import datetime

        results = []
        try:
            root = ET.fromstring(raw_response)
            entries = root.findall("atom:entry", ArxivSearchEngine.NS)

            for entry in entries:
                title = entry.find("atom:title", ArxivSearchEngine.NS)
                summary = entry.find("atom:summary", ArxivSearchEngine.NS)
                published = entry.find("atom:published", ArxivSearchEngine.NS)
                pdf_link = entry.find("atom:link[@title='pdf']", ArxivSearchEngine.NS)
                abs_link = entry.find(
                    "atom:link[@rel='alternate']", ArxivSearchEngine.NS
                )

                authors = []
                for author in entry.findall(
                    "atom:author/atom:name", ArxivSearchEngine.NS
                ):
                    if author.text:
                        authors.append(author.text)

                # Parse date from ISO format (YYYY-MM-DDTHH:MM:SSZ)
                parsed_date = None
                if published is not None and published.text:
                    try:
                        parsed_date = datetime.fromisoformat(published.text[:10]).date()
                    except ValueError:
                        pass

                pdf_url = pdf_link.get("href", "") if pdf_link is not None else ""
                abs_url = abs_link.get("href", "") if abs_link is not None else ""
                result = SearchResult(
                    title=title.text.strip()
                    if title is not None and title.text
                    else "",
                    url=abs_url,
                    source="arxiv",
                    snippet=summary.text.strip()[:500]
                    if summary is not None and summary.text
                    else None,
                    pdf_url=pdf_url,
                    authors=authors,
                    published_date=parsed_date,
                    relevance_score=0.8,
                )
                results.append(result)
        except ET.ParseError as e:
            logger.error(f"Failed to parse arXiv XML response: {e}")

        return results
