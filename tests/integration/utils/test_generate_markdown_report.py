from datetime import datetime

from tests.integration import schemas
from tests.integration.utils.generate_markdown_report import generate_markdown_report


def test_generate_markdown_report_collapses_artifacts_and_details():
    model_result = schemas.ModelTestResults(
        model_name="test-model",
        run_suffix="test_run",
        llm_config={},
        timestamp=datetime(2026, 1, 1, 12, 0, 0),
        test_instances=[
            schemas.TestInstanceResult(
                instance_id="t01_example",
                test_result=schemas.TestResultData(success=True),
                test_type="integration",
                required=True,
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
    consolidated = schemas.ConsolidatedResults(
        timestamp=datetime(2026, 1, 1, 12, 30, 0),
        total_models=1,
        model_results=[model_result],
        overall_success_rate=1.0,
        total_cost_all_models=0.12,
    )

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
