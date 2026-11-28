from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
WORKFLOW = WORKFLOWS / "update-version-pins.yml"


def test_all_pin_refreshers_live_in_one_workflow() -> None:
    text = WORKFLOW.read_text()
    assert "name: Update version pins" in text
    for job in ("update-npm:", "update-code-server:", "update-debian-snapshot:"):
        assert job in text
    for legacy in (
        "update-npm-pin.yml",
        "update-code-server-pin.yml",
        "update-debian-snapshot.yml",
    ):
        assert not (WORKFLOWS / legacy).exists(), legacy


def test_each_job_keeps_its_own_cadence() -> None:
    text = WORKFLOW.read_text()
    # Union of schedules on the single trigger.
    for cron in ("37 6 * * *", "47 6 * * 1", "17 6 * * 1"):
        assert f"cron: {cron}" in text
    # Each job is gated to its own schedule, or to a manual dispatch
    # naming that pin (or all).
    gates = {
        "update-npm": ("'37 6 * * *'", "inputs.pin == 'npm'"),
        "update-code-server": ("'47 6 * * 1'", "inputs.pin == 'code-server'"),
        "update-debian-snapshot": ("'17 6 * * 1'", "inputs.pin == 'debian'"),
    }
    for job, (cron, pin) in gates.items():
        block = text.split(f"    {job}:", 1)[1].split("\n    update-", 1)[0]
        assert "github.event.schedule ==" in block
        assert cron in block
        assert pin in block
        assert "inputs.pin == 'all'" in block
