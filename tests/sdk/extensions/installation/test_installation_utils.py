import pytest

from openhands.sdk.extensions.installation.utils import validate_extension_name


@pytest.mark.parametrize(
    "input, valid",
    [
        ("", False),
        ("kebab-case", True),
        ("simple", True),
        ("CamelCase", False),
        ("---", False),
    ],
)
def test_validate_extension_name(input: str, valid: bool):
    """Tests that validate_extension_name captures kebab-case."""
    if valid:
        assert validate_extension_name(input) is None
    else:
        with pytest.raises(ValueError):
            validate_extension_name(input)
