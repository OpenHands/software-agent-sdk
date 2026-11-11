"""Send the repository's malformed-history patterns to the run's real model.

Usage: api_compliance.py [pattern,...]   (default: every pattern)

The patterns are the API compliance tests in `tests/integration/tests/a*.py`
(an unmatched tool_use, a tool result with the wrong id, an interleaved
message, ...). Their own runner, `tests/integration/api_compliance/
run_compliance.py`, only knows the All-Hands proxy models, so this program
loads the same test classes and runs each one against the run's model, the
way `run_compliance.py` does for each of its models.

For every pattern it reports how the provider answered (`accepted`,
`rejected`, ...) and, for a rejection, which exception the SDK raised. A
rejection should surface as one of the SDK's typed `LLM*Error`s, not a raw
LiteLLM or HTTP error.
"""

import sys

from sdk_verify import emit, make_llm

from tests.integration.api_compliance.run_compliance import load_compliance_tests


wanted = sys.argv[1].split(",") if len(sys.argv) > 1 and sys.argv[1] else None
llm = make_llm(usage_id="compliance").model_copy(
    update={"num_retries": 0, "caching_prompt": False}
)

results: dict[str, dict] = {}
for _, test_class in load_compliance_tests(wanted):
    test = test_class()
    outcome = test.run_test(llm, model_id=llm.model)
    results[test.pattern_name] = {
        "response_type": str(outcome.response_type),
        "error_type": outcome.error_type,
        "error": (outcome.error_message or "")[:300] or None,
    }

rejected = {k: v for k, v in results.items() if v["response_type"] == "rejected"}
emit(
    {
        "ok": True,
        "model": llm.model,
        "patterns": sorted(results),
        "results": results,
        "rejected": sorted(rejected),
        "rejections_typed": all(
            (v["error_type"] or "").startswith("LLM") for v in rejected.values()
        ),
    }
)
