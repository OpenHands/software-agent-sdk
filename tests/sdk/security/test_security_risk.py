"""Comprehensive tests for SecurityRisk enum and is_riskier functionality."""

from itertools import product

import pytest

from openhands.sdk.security.risk import SecurityRisk


def test_security_risk_enum_values():
    """Test that SecurityRisk enum has expected values."""
    assert SecurityRisk.UNKNOWN == "UNKNOWN"
    assert SecurityRisk.LOW == "LOW"
    assert SecurityRisk.MEDIUM == "MEDIUM"
    assert SecurityRisk.HIGH == "HIGH"


def test_security_risk_string_representation():
    """Test string representation of SecurityRisk values."""
    assert str(SecurityRisk.UNKNOWN) == "UNKNOWN"
    assert str(SecurityRisk.LOW) == "LOW"
    assert str(SecurityRisk.MEDIUM) == "MEDIUM"
    assert str(SecurityRisk.HIGH) == "HIGH"


def test_riskiness_ordering():
    """Test basic ordering with is_riskier method."""
    # Test the natural risk ordering: LOW < MEDIUM < HIGH
    assert SecurityRisk.MEDIUM.is_riskier(SecurityRisk.LOW)
    assert SecurityRisk.HIGH.is_riskier(SecurityRisk.MEDIUM)
    assert SecurityRisk.HIGH.is_riskier(SecurityRisk.LOW)

    # Test the reverse ordering (should be False)
    assert not SecurityRisk.LOW.is_riskier(SecurityRisk.MEDIUM)
    assert not SecurityRisk.MEDIUM.is_riskier(SecurityRisk.HIGH)
    assert not SecurityRisk.LOW.is_riskier(SecurityRisk.HIGH)


@pytest.mark.parametrize(
    "risk_level",
    [
        SecurityRisk.LOW,
        SecurityRisk.MEDIUM,
        SecurityRisk.HIGH,
    ],
)
def test_riskiness_ordering_is_reflexive(risk_level):
    """Test that is_riskier is reflexive by default."""
    assert risk_level.is_riskier(risk_level)


@pytest.mark.parametrize(
    "risk_level",
    [
        SecurityRisk.LOW,
        SecurityRisk.MEDIUM,
        SecurityRisk.HIGH,
    ],
)
def test_riskiness_ordering_non_reflexive(risk_level):
    """Test that is_riskier with reflexive=False is non-reflexive."""
    assert not risk_level.is_riskier(risk_level, reflexive=False)


def test_riskiness_ordering_undefined_for_unknown():
    """Test that comparisons involving UNKNOWN raise ValueError."""
    for first_risk, second_risk in product(list(SecurityRisk), repeat=2):
        if SecurityRisk.UNKNOWN in (first_risk, second_risk):
            with pytest.raises(ValueError):
                first_risk.is_riskier(second_risk)

        # If there's no UNKNOWN, the comparison should work. To test this we'll call the
        # function and make sure it returned a boolean.
        else:
            comparison = first_risk.is_riskier(second_risk)
            assert comparison in (True, False)


def test_security_risk_get_color():
    """Test that SecurityRisk.get_color() returns expected color codes."""
    assert SecurityRisk.LOW.get_color() == "green"
    assert SecurityRisk.MEDIUM.get_color() == "yellow"
    assert SecurityRisk.HIGH.get_color() == "red"
    assert SecurityRisk.UNKNOWN.get_color() == "white"


def test_lt_ordering():
    """Test that __lt__ follows LOW < MEDIUM < HIGH."""
    assert SecurityRisk.LOW < SecurityRisk.MEDIUM
    assert SecurityRisk.MEDIUM < SecurityRisk.HIGH
    assert SecurityRisk.LOW < SecurityRisk.HIGH


def test_lt_not_less_than_self():
    """Test that no risk level is less than itself."""
    assert not SecurityRisk.LOW < SecurityRisk.LOW
    assert not SecurityRisk.MEDIUM < SecurityRisk.MEDIUM
    assert not SecurityRisk.HIGH < SecurityRisk.HIGH


def test_lt_reverse_ordering():
    """Test that higher is not less than lower."""
    assert not SecurityRisk.HIGH < SecurityRisk.LOW
    assert not SecurityRisk.HIGH < SecurityRisk.MEDIUM
    assert not SecurityRisk.MEDIUM < SecurityRisk.LOW


def test_lt_unknown_raises():
    """Test that comparing UNKNOWN raises ValueError, consistent with is_riskier."""
    with pytest.raises(ValueError):
        SecurityRisk.UNKNOWN < SecurityRisk.LOW
    with pytest.raises(ValueError):
        SecurityRisk.LOW < SecurityRisk.UNKNOWN
    with pytest.raises(ValueError):
        SecurityRisk.UNKNOWN < SecurityRisk.UNKNOWN


def test_max_on_concrete_risks():
    """Test that max() works on concrete risk lists via __lt__."""
    assert (
        max([SecurityRisk.LOW, SecurityRisk.MEDIUM, SecurityRisk.HIGH])
        == SecurityRisk.HIGH
    )
    assert max([SecurityRisk.LOW, SecurityRisk.LOW]) == SecurityRisk.LOW
    assert max([SecurityRisk.MEDIUM, SecurityRisk.HIGH]) == SecurityRisk.HIGH
