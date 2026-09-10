from datetime import datetime

from tests.integration import schemas
from tests.integration.utils.generate_markdown_report import (
    derive_report_title,
    generate_markdown_report,
)


def _model_result(test_type: str = "integration") -> schemas.ModelTestResults:
    return schemas.ModelTestResults(
        model_name="test-model",
        run_suffix="test_run",
        llm_config={},
        timestamp=datetime(2026, 1, 1, 12, 0, 0),
        test_instances=[
            schemas.TestInstanceResult(
                instance_id="t01_example",
                test_result=schemas.TestResultData(success=True),
                test_type=test_type,
                required=test_type == "integration",
                cost=0.12,
            )
        ],
        total_tests=1,
        successful_tests=1,
        skipped_tests=0,
        success_rate=1.0,
        total_cost=0.12,
        total_token_usage=schemas.TokenUsageData(prompt_tokens=10, completion_tokens=5),
        artifact_url="https://example.com/artifact",
    )


def _consolidated(
    model_result: schemas.ModelTestResults,
) -> schemas.ConsolidatedResults:
    return schemas.ConsolidatedResults(
        timestamp=datetime(2026, 1, 1, 12, 30, 0),
        total_models=1,
        model_results=[model_result],
        overall_success_rate=1.0,
        total_cost_all_models=0.12,
    )


def test_generate_markdown_report_collapses_artifacts_and_details():
    consolidated = _consolidated(_model_result("integration"))

    report = generate_markdown_report(consolidated)

    details_start = report.index("<details>")
    details = report[details_start : report.index("</details>")]
    visible_summary = report[:details_start]

    assert "**Overall Success Rate**: 100.0%" in visible_summary
    assert "**Total Cost**: $0.12" in visible_summary
    assert "<summary>📁 Detailed Logs & Artifacts</summary>" in details
    assert "[📥 View & Download Logs](https://example.com/artifact)" in details
    assert "## 📊 Summary" in details
    assert "## 📋 Detailed Results" in details
    assert "## 📊 Summary" not in visible_summary


def test_report_title_reflects_test_type():
    assert derive_report_title(_consolidated(_model_result("integration"))) == (
        "# 🧪 Integration Tests Results"
    )
    assert derive_report_title(_consolidated(_model_result("behavior"))) == (
        "# 🧪 Behavior Tests Results"
    )
    assert derive_report_title(_consolidated(_model_result("condenser"))) == (
        "# 🧪 Condenser Tests Results"
    )


def test_report_title_defaults_to_integration_for_mixed_types():
    mixed = schemas.ConsolidatedResults(
        timestamp=datetime(2026, 1, 1, 12, 30, 0),
        total_models=1,
        model_results=[
            schemas.ModelTestResults(
                model_name="test-model",
                run_suffix="test_run",
                llm_config={},
                test_instances=[
                    schemas.TestInstanceResult(
                        instance_id="t01_integration",
                        test_result=schemas.TestResultData(success=True),
                        test_type="integration",
                        required=True,
                        cost=0.1,
                    ),
                    schemas.TestInstanceResult(
                        instance_id="t01_behavior",
                        test_result=schemas.TestResultData(success=True),
                        test_type="behavior",
                        required=False,
                        cost=0.02,
                    ),
                ],
                total_tests=2,
                successful_tests=2,
                skipped_tests=0,
                success_rate=1.0,
                total_cost=0.12,
            )
        ],
        overall_success_rate=1.0,
        total_cost_all_models=0.12,
    )

    assert derive_report_title(mixed) == "# 🧪 Integration Tests Results"
    behavior_report = generate_markdown_report(_consolidated(_model_result("behavior")))
    assert behavior_report.startswith("# 🧪 Behavior Tests Results")
