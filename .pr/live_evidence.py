import tempfile

from openhands.tools.terminal.definition import TerminalAction
from openhands.tools.terminal.terminal import create_terminal_session


COMMAND = """printf 'prefix\\n'; python - <<'PY'
print('first heredoc')
PY
printf 'between\\n'; python - <<'PY'
print('second heredoc')
PY
"""

with tempfile.TemporaryDirectory() as work_dir:
    session = create_terminal_session(work_dir=work_dir, terminal_type="subprocess")
    session.initialize()
    try:
        observation = session.execute(TerminalAction(command=COMMAND))
        print(f"is_error={observation.is_error}")
        print(f"exit_code={observation.metadata.exit_code}")
        print(observation.text)
    finally:
        session.close()
