"""Provenance of the LiteLLM model database this process loaded (#4880)."""

from openhands.sdk.llm.utils.model_cost_map import model_cost_map_provenance


class TestProvenance:
    def test_reports_what_this_process_actually_loaded(self):
        provenance = model_cost_map_provenance()

        assert provenance.source in {"remote", "local", "unknown"}
        assert provenance.model_count > 0
        assert len(provenance.fingerprint) == 64

    def test_fingerprint_is_stable_within_a_process(self):
        """Two readings must agree, or it is useless for correlating hosts."""
        assert (
            model_cost_map_provenance().fingerprint
            == model_cost_map_provenance().fingerprint
        )

    def test_records_where_the_data_came_from(self):
        """The question this exists to answer: which document is in play.

        Two hosts disagreeing about a model's capabilities are indistinguishable
        today, and a fallback to the wheel's bundled copy leaves no trace.
        """
        provenance = model_cost_map_provenance()

        assert provenance.source in {"remote", "local", "unknown"}
        if provenance.source == "remote":
            assert provenance.url
        assert isinstance(provenance.is_env_forced, bool)
