"""Runtime state of an LLM, kept apart from its configuration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openhands.sdk.llm.utils.metrics import Metrics
from openhands.sdk.llm.utils.telemetry import Telemetry


if TYPE_CHECKING:
    from openhands.sdk.llm.llm import LLM


class LLMSession:
    """What accumulates while an LLM is used: usage metrics and telemetry.

    An :class:`LLM` describes how to call a model. A session holds what calling
    it produces, so one configuration can serve several users without their
    usage, callbacks or in-flight request state running together.

    There are two kinds of owner:

    * every ``LLM`` has a session of its own, behind ``llm.metrics`` and
      ``llm.telemetry``. It counts all the usage of that object.
    * a conversation's :class:`LLMRegistry` keeps one session per role
      (``usage_id``). A call made with that conversation's
      :class:`LLMCallContext` records there, whichever ``LLM`` object fills the
      role at the time.

    The two are the same session while an LLM serves a single conversation:
    the registry takes over the session of the first LLM it gets for a role.
    They come apart when one LLM object is used by a second conversation, so
    that neither conversation sees the other's usage.
    """

    __slots__ = ("_telemetry",)

    def __init__(self, llm: LLM) -> None:
        self._telemetry: Telemetry = Telemetry(
            model_name=llm.model,
            log_enabled=llm.log_completions,
            log_dir=_log_dir(llm),
            input_cost_per_token=llm.input_cost_per_token,
            output_cost_per_token=llm.output_cost_per_token,
            metrics=Metrics(model_name=llm.model),
        )

    def __eq__(self, other: object) -> bool:
        # By value, as the metrics and telemetry it holds compare: two LLMs
        # with equal configuration and equal usage are equal.
        if not isinstance(other, LLMSession):
            return NotImplemented
        return self._telemetry == other._telemetry

    @property
    def metrics(self) -> Metrics:
        """Usage recorded in this session."""
        return self._telemetry.metrics

    @property
    def telemetry(self) -> Telemetry:
        """Telemetry handler that records into :attr:`metrics`."""
        return self._telemetry

    def follow(self, llm: LLM) -> None:
        """Serve ``llm``, the LLM now calling in this session's role.

        The metrics and the telemetry callbacks belong to the role and stay.
        The settings telemetry takes from an LLM follow the caller, and unless
        this is the LLM's own session, ``llm.metrics`` also counts what is
        recorded here.
        """
        telemetry = self._telemetry
        telemetry.model_name = llm.model
        telemetry.log_enabled = llm.log_completions
        telemetry.log_dir = _log_dir(llm)
        telemetry.input_cost_per_token = llm.input_cost_per_token
        telemetry.output_cost_per_token = llm.output_cost_per_token

        own = llm.session.metrics
        self.metrics.roll_up_into(None if own is self.metrics else own)

    def restore_metrics(self, metrics: Metrics) -> None:
        """Continue from ``metrics``, e.g. those persisted with a conversation.

        The session records into the given object from now on. What that object
        already holds is added once to the ledger this session rolls up into.
        """
        current = self.metrics
        if metrics is current:
            return
        target = current.rolls_up_into
        current.roll_up_into(None)
        if target is not None and target is not metrics:
            target.merge(metrics)
            metrics.roll_up_into(target)
        self._telemetry.metrics = metrics


def _log_dir(llm: LLM) -> str | None:
    return llm.log_completions_folder if llm.log_completions else None
