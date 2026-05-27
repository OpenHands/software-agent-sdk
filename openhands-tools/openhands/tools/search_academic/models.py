"""Data models for academic search tools."""

from datetime import date

from pydantic import BaseModel, Field


class SearchResult(BaseModel):
    """A single search result."""

    title: str = Field(..., description="Result title")
    url: str = Field(..., description="Result URL")
    source: str = Field(
        ...,
        description="Search engine source (serper, scholar, arxiv, pubmed, openalex)",
    )
    snippet: str | None = Field(
        default=None,
        description="Short snippet or abstract",
    )
    pdf_url: str | None = Field(
        default=None,
        description="Direct PDF download link when available (e.g. openAccessPdf.url)",
    )
    authors: list[str] = Field(
        default_factory=list,
        description="List of author names",
    )
    published_date: date | None = Field(
        default=None,
        description="Publication date",
    )
    relevance_score: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Relevance score from 0 to 1",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "title": "Machine Learning Basics",
                    "url": "https://example.com/paper",
                    "source": "scholar",
                    "snippet": "A comprehensive introduction...",
                    "authors": ["John Doe"],
                    "published_date": "2023-01-15",
                    "relevance_score": 0.95,
                }
            ]
        }
    }


class SearchQuery(BaseModel):
    """Search query configuration."""

    query: str = Field(..., min_length=1, description="Search query string")
    engines: list[str] = Field(
        default_factory=list,
        description="List of search engines to use",
    )
    max_results: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum number of results to return",
    )
    language: str = Field(
        default="en",
        description="Language code (e.g., 'en', 'zh-cn')",
    )


class SearchResponse(BaseModel):
    """Response from a search engine."""

    query: str = Field(..., description="Original search query")
    results: list[SearchResult] = Field(
        default_factory=list,
        description="List of search results",
    )
    total_found: int = Field(
        default=0,
        ge=0,
        description="Total number of results found",
    )
    engine: str = Field(..., description="Search engine name")
    execution_time: float = Field(
        default=0.0,
        ge=0.0,
        description="Execution time in seconds",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "query": "machine learning",
                    "results": [
                        {
                            "title": "ML Paper",
                            "url": "https://example.com/1",
                            "source": "scholar",
                            "relevance_score": 0.9,
                        }
                    ],
                    "total_found": 100,
                    "engine": "scholar",
                    "execution_time": 0.5,
                }
            ]
        }
    }
