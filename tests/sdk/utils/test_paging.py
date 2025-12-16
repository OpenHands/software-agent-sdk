"""Tests for the paging utility functions."""

from dataclasses import dataclass
from typing import Any

import pytest

from openhands.sdk.utils.paging import page_iterator


@dataclass
class MockPage:
    """Mock page object for testing."""

    items: list[Any]
    next_page_id: str | None = None


class MockSearchService:
    """Mock search service for testing pagination."""

    def __init__(self, all_items: list[Any], page_size: int = 2):
        self.all_items = all_items
        self.page_size = page_size

    async def search(self, page_id: str | None = None, **kwargs) -> MockPage:
        """Mock search method that returns paginated results."""
        start_index = 0

        # Find starting index based on page_id
        if page_id:
            try:
                start_index = int(page_id)
            except (ValueError, TypeError):
                start_index = 0

        # Get items for this page
        end_index = start_index + self.page_size
        page_items = self.all_items[start_index:end_index]

        # Determine next_page_id
        next_page_id = None
        if end_index < len(self.all_items):
            next_page_id = str(end_index)

        return MockPage(items=page_items, next_page_id=next_page_id)


@pytest.mark.asyncio
async def test_page_iterator_empty_results():
    """Test page_iterator with empty results."""
    service = MockSearchService([])

    items = []
    async for item in page_iterator(service.search):
        items.append(item)

    assert items == []


@pytest.mark.asyncio
async def test_page_iterator_single_page():
    """Test page_iterator with results that fit in a single page."""
    service = MockSearchService(["item1", "item2"], page_size=5)

    items = []
    async for item in page_iterator(service.search):
        items.append(item)

    assert items == ["item1", "item2"]


@pytest.mark.asyncio
async def test_page_iterator_multiple_pages():
    """Test page_iterator with results spanning multiple pages."""
    service = MockSearchService(
        ["item1", "item2", "item3", "item4", "item5"], page_size=2
    )

    items = []
    async for item in page_iterator(service.search):
        items.append(item)

    assert items == ["item1", "item2", "item3", "item4", "item5"]


@pytest.mark.asyncio
async def test_page_iterator_with_kwargs():
    """Test page_iterator passing through keyword arguments."""
    service = MockSearchService(["a", "b", "c", "d"], page_size=2)

    # Mock search method that accepts additional kwargs
    async def search_with_filter(
        page_id: str | None = None, filter_value: str | None = None
    ) -> MockPage:
        page = await service.search(page_id=page_id)
        if filter_value:
            # Filter items based on the filter_value
            filtered_items = [item for item in page.items if filter_value in item]
            return MockPage(items=filtered_items, next_page_id=page.next_page_id)
        return page

    items = []
    async for item in page_iterator(search_with_filter, filter_value="a"):
        items.append(item)

    assert items == ["a"]


@pytest.mark.asyncio
async def test_page_iterator_with_args():
    """Test page_iterator passing through positional arguments."""
    service = MockSearchService(["x", "y", "z"], page_size=2)

    # Mock search method that accepts positional args
    async def search_with_args(prefix: str, page_id: str | None = None) -> MockPage:
        page = await service.search(page_id=page_id)
        # Prefix each item
        prefixed_items = [f"{prefix}{item}" for item in page.items]
        return MockPage(items=prefixed_items, next_page_id=page.next_page_id)

    items = []
    async for item in page_iterator(search_with_args, "prefix_"):
        items.append(item)

    assert items == ["prefix_x", "prefix_y", "prefix_z"]


@pytest.mark.asyncio
async def test_page_iterator_preserves_initial_page_id():
    """Test that page_iterator respects an initial page_id in kwargs."""
    service = MockSearchService(["a", "b", "c", "d", "e"], page_size=2)

    # Start from the second page (index 2)
    items = []
    async for item in page_iterator(service.search, page_id="2"):
        items.append(item)

    assert items == ["c", "d", "e"]


@pytest.mark.asyncio
async def test_page_iterator_removes_page_id_from_kwargs():
    """Test that page_iterator properly handles page_id in kwargs."""
    service = MockSearchService(["1", "2", "3"], page_size=1)

    # Mock search that would fail if page_id appears twice
    call_count = 0

    async def strict_search(page_id: str | None = None, **kwargs) -> MockPage:
        nonlocal call_count
        call_count += 1

        # Ensure no extra page_id in kwargs
        assert "page_id" not in kwargs

        return await service.search(page_id=page_id)

    items = []
    async for item in page_iterator(strict_search, page_id="1", other_param="value"):
        items.append(item)

    assert items == ["2", "3"]
    assert call_count == 2  # Should make 2 calls (starting from page_id="1")


@pytest.mark.asyncio
async def test_page_iterator_complex_objects():
    """Test page_iterator with complex objects."""

    @dataclass
    class ComplexItem:
        id: int
        name: str

    complex_items = [
        ComplexItem(1, "first"),
        ComplexItem(2, "second"),
        ComplexItem(3, "third"),
    ]

    service = MockSearchService(complex_items, page_size=2)

    items = []
    async for item in page_iterator(service.search):
        items.append(item)

    assert len(items) == 3
    assert items[0].id == 1
    assert items[0].name == "first"
    assert items[1].id == 2
    assert items[1].name == "second"
    assert items[2].id == 3
    assert items[2].name == "third"
