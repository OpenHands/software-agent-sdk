from __future__ import annotations

from datetime import date, timedelta

import pytest
from deprecation import DeprecatedWarning

from openhands.sdk.utils.deprecation import (
    deprecated,
    warn_cleanup,
    warn_deprecated,
)


def test_warn_deprecated_uses_project_versions() -> None:
    with pytest.warns(DeprecatedWarning) as caught:
        warn_deprecated(
            "tests.api",
            deprecated_in="1.1.0",
            removed_in="2.0.0",
            details="Use tests.new_api()",
        )

    message = str(caught[0].message)
    assert "as of 1.1.0" in message
    assert "removed in 2.0.0" in message
    assert "Use tests.new_api()" in message


def test_deprecated_decorator_warns_and_preserves_call() -> None:
    @deprecated(
        deprecated_in="1.1.0",
        removed_in="2.0.0",
        details="Use replacement()",
    )
    def old(x: int) -> int:
        return x * 2

    with pytest.warns(DeprecatedWarning):
        assert old(3) == 6


@pytest.mark.parametrize(
    ("deprecated_in", "removed_in", "current_version"),
    [("0.1", "0.3", "0.2"), ("2024.1", "2025.1", "2024.4")],
)
def test_deprecated_decorator_allows_version_overrides(
    deprecated_in: str, removed_in: str, current_version: str
) -> None:
    @deprecated(
        deprecated_in=deprecated_in,
        removed_in=removed_in,
        current_version=current_version,
    )
    def legacy() -> None:
        return None

    with pytest.warns(DeprecatedWarning) as caught:
        legacy()

    message = str(caught[0].message)
    assert f"as of {deprecated_in}" in message
    assert f"removed in {removed_in}" in message


def test_warn_deprecated_allows_indefinite_removal() -> None:
    with pytest.warns(DeprecatedWarning):
        warn_deprecated(
            "tests.indefinite",
            deprecated_in="1.1.0",
            removed_in=None,
            details="Use tests.indefinite_replacement()",
        )


def test_deprecated_decorator_supports_indefinite_removal() -> None:
    @deprecated(
        deprecated_in="1.1.0",
        removed_in=None,
        details="Use replacement()",
    )
    def legacy() -> None:
        return None

    with pytest.warns(DeprecatedWarning):
        legacy()


def test_warn_cleanup_with_version_deadline() -> None:
    with pytest.warns(UserWarning) as caught:
        warn_cleanup(
            "Temporary workaround for library X",
            cleanup_by="1.1.0",
            current_version="1.2.0",
            details="Remove when library X adds feature Y",
        )

    message = str(caught[0].message)
    assert "Cleanup required" in message
    assert "Temporary workaround for library X" in message
    assert "scheduled for removal by 1.1.0" in message
    assert "Remove when library X adds feature Y" in message


def test_warn_cleanup_with_date_deadline() -> None:
    yesterday = date.today() - timedelta(days=1)
    with pytest.warns(UserWarning) as caught:
        warn_cleanup(
            "Temporary API shim",
            cleanup_by=yesterday,
            details="Remove after API stabilizes",
        )

    message = str(caught[0].message)
    assert "Cleanup required" in message
    assert "Temporary API shim" in message
    assert "Remove after API stabilizes" in message


def test_warn_cleanup_before_deadline_no_warning() -> None:
    import warnings

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        warn_cleanup(
            "Future cleanup item",
            cleanup_by="99.0.0",
            current_version="1.2.0",
        )

    assert len(caught) == 0


def test_warn_cleanup_date_in_future_no_warning() -> None:
    import warnings

    tomorrow = date.today() + timedelta(days=1)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        warn_cleanup(
            "Future cleanup item",
            cleanup_by=tomorrow,
        )

    assert len(caught) == 0
