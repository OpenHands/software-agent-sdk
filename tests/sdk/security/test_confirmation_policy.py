"""Tests for ConfirmationPolicy classes and serialization."""

import pytest
from pydantic import BaseModel, ValidationError

from openhands.sdk.security.confirmation_policy import (
    AlwaysConfirm,
    ConfirmationPolicyBase,
    ConfirmRisky,
    NeverConfirm,
)
from openhands.sdk.security.risk import SecurityRisk


class TestConfirmationPolicyBase:
    """Tests for the ConfirmationPolicy base class."""

    def test_cannot_instantiate_base_class(self) -> None:
        """Test that the base class cannot be instantiated directly."""
        with pytest.raises(TypeError):
            # Of course mypy doesn't want us to do this, so ignore the type check while
            # we confirm the runtime behavior.
            ConfirmationPolicyBase()  # type: ignore

    @pytest.mark.parametrize("cls", list(ConfirmationPolicyBase.__subclasses__()))
    def test_confirmation_policy_container_serialization(
        self, cls: type[ConfirmationPolicyBase]
    ) -> None:
        """Test that a container model with ConfirmationPolicy instances as a field can
        be serialized.
        """

        class PolicyContainer(BaseModel):
            policy: ConfirmationPolicyBase

        container = PolicyContainer(policy=cls())

        container_dict = container.model_dump_json()
        restored_container = PolicyContainer.model_validate_json(container_dict)

        assert isinstance(restored_container.policy, cls)
        assert container.policy == restored_container.policy


class TestAlwaysConfirm:
    """Tests for the AlwaysConfirm policy."""

    @pytest.mark.parametrize("risk", list(SecurityRisk))
    def test_always_confirm(self, risk: SecurityRisk) -> None:
        """Test that the policy always confirms, regardless of the inputs."""
        policy = AlwaysConfirm()
        assert policy.should_confirm(risk) is True

    def test_roundtrip_serialization(self) -> None:
        """Test that AlwaysConfirm can be serialized and deserialized correctly."""
        policy = AlwaysConfirm()
        policy_dict = policy.model_dump_json()
        restored_policy = AlwaysConfirm.model_validate_json(policy_dict)

        assert isinstance(restored_policy, AlwaysConfirm)

    def test_polymorphic_serialization(self) -> None:
        """Test polymorphic serialization and deserialization. This requires we
        deserialize using the base class.
        """
        policy: ConfirmationPolicyBase = AlwaysConfirm()
        policy_dict = policy.model_dump_json()
        restored_policy = ConfirmationPolicyBase.model_validate_json(policy_dict)

        assert isinstance(restored_policy, AlwaysConfirm)


class TestNeverConfirm:
    """Tests for the NeverConfirm policy."""

    @pytest.mark.parametrize("risk", list(SecurityRisk))
    def test_never_confirm(self, risk: SecurityRisk) -> None:
        """Test that the policy never confirms, regardless of the inputs."""
        policy = NeverConfirm()
        assert policy.should_confirm(risk) is False

    def test_roundtrip_serialization(self) -> None:
        """Test that NeverConfirm can be serialized and deserialized correctly."""
        policy = NeverConfirm()
        policy_dict = policy.model_dump_json()
        restored_policy = NeverConfirm.model_validate_json(policy_dict)

        assert isinstance(restored_policy, NeverConfirm)

    def test_polymorphic_serialization(self) -> None:
        """Test polymorphic serialization and deserialization. This requires we
        deserialize using the base class.
        """
        policy: ConfirmationPolicyBase = NeverConfirm()
        policy_dict = policy.model_dump_json()
        restored_policy = ConfirmationPolicyBase.model_validate_json(policy_dict)

        assert isinstance(restored_policy, NeverConfirm)


@pytest.mark.parametrize(
    ("threshold", "risk", "expected"),
    [
        (SecurityRisk.HIGH, SecurityRisk.HIGH, True),
        (SecurityRisk.HIGH, SecurityRisk.MEDIUM, False),
        (SecurityRisk.MEDIUM, SecurityRisk.MEDIUM, True),
        (SecurityRisk.MEDIUM, SecurityRisk.LOW, False),
        (SecurityRisk.LOW, SecurityRisk.LOW, True),
    ],
)
def test_confirm_risky_threshold_is_inclusive(threshold, risk, expected) -> None:
    assert ConfirmRisky(threshold=threshold).should_confirm(risk) is expected


@pytest.mark.parametrize("confirm_unknown", [True, False])
def test_confirm_risky_unknown_risk_follows_flag(confirm_unknown: bool) -> None:
    policy = ConfirmRisky(confirm_unknown=confirm_unknown)

    assert policy.should_confirm(SecurityRisk.UNKNOWN) is confirm_unknown
    assert policy.should_confirm() is confirm_unknown


def test_confirm_risky_rejects_unknown_threshold() -> None:
    with pytest.raises(ValidationError, match="Threshold cannot be UNKNOWN"):
        ConfirmRisky(threshold=SecurityRisk.UNKNOWN)


def test_confirm_risky_rejects_unknown_threshold_when_deserialized() -> None:
    payload = ConfirmRisky().model_dump()
    payload["threshold"] = "UNKNOWN"

    with pytest.raises(ValidationError, match="Threshold cannot be UNKNOWN"):
        ConfirmationPolicyBase.model_validate(payload)


def test_confirm_risky_polymorphic_roundtrip_keeps_settings() -> None:
    policy: ConfirmationPolicyBase = ConfirmRisky(
        threshold=SecurityRisk.MEDIUM, confirm_unknown=False
    )

    restored = ConfirmationPolicyBase.model_validate_json(policy.model_dump_json())

    assert restored == policy
