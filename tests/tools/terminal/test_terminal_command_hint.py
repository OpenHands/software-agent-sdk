from openhands.tools.terminal.impl import _detect_command_hint


def test_detect_rg_simple():
    hint, argv = _detect_command_hint("rg foo")
    assert hint == "terminal:rg"
    assert argv and argv[0] == "rg"


def test_detect_rg_with_env_and_sudo():
    hint, argv = _detect_command_hint("RIPGREP_CONFIG_PATH=cfg sudo rg --json pattern .")
    assert hint == "terminal:rg"
    assert argv and "rg" in argv


def test_detect_non_special_command():
    hint, argv = _detect_command_hint("echo hello")
    assert hint is None
    assert argv == ["echo", "hello"]

    hint2, argv2 = _detect_command_hint("which rg")
    assert hint2 is None
    assert argv2 == ["which", "rg"]


def test_detect_rg_full_path():
    hint, argv = _detect_command_hint("/usr/bin/rg foo bar")
    assert hint == "terminal:rg"
    assert argv and argv[0].endswith("rg")


def test_detect_ripgrep_alias():
    hint, argv = _detect_command_hint("ripgrep pattern .")
    assert hint == "terminal:rg"
    assert argv and argv[0] == "ripgrep"


def test_detect_empty_or_invalid_command():
    hint, argv = _detect_command_hint("")
    assert hint is None
    assert argv is None

    hint2, argv2 = _detect_command_hint("echo \"unterminated")
    assert hint2 is None
    assert argv2 is None
