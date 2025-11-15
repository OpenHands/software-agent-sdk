from __future__ import annotations

import pytest
from deprecation import DeprecatedWarning

from openhands.sdk.utils.deprecation import (
    deprecated,
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
