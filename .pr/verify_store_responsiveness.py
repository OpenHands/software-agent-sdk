"""Run with uv run python .pr/verify_store_responsiveness.py RUN_DIR."""

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

from filelock import FileLock


run = Path(sys.argv[1]).resolve()
cli = Path('.agents/skills/verify-agent-server/scripts/control-agent-server').resolve()
cases = [
    ('models', 'profiles/.profiles.lock', 'GET', '/v1/models', None, 200),
    (
        'settings', 'profiles/.profiles.lock', 'PATCH', '/api/settings',
        {'active_profile': 'qa-missing'}, 404,
    ),
    (
        'scoped-secrets', 'agent-profiles/.agent-profiles.lock', 'GET',
        '/api/settings/secrets?agent_profile_id=qa-missing', None, 404,
    ),
]
failed = False
for name, lock_file, method, path, payload, status in cases:
    lock_path = run / 'home/.openhands' / lock_file
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    held = FileLock(lock_path, thread_local=False)
    held.acquire()
    safety = threading.Timer(5, held.release)
    safety.start()
    command = [
        str(cli), 'api', method, path, '--run', str(run), '--auth', 'bearer' if path == '/v1/models' else 'header',
        '--expect', str(status), '--quiet', '--save', f'PR5794/{name}',
    ]
    if payload is not None:
        command.extend(['--json', json.dumps(payload)])
    waiting = subprocess.Popen(command, stdout=subprocess.PIPE, text=True)
    try:
        time.sleep(0.5)
        alive = subprocess.run(
            [
                str(cli), 'api', 'GET', '/alive', '--run', str(run),
                '--expect', '200', '--expect-max-ms', '2000', '--quiet',
                '--save', f'PR5794/{name}-alive',
            ],
            capture_output=True, text=True, timeout=15,
        )
        pending = waiting.poll() is None
        held.release()
        output, _ = waiting.communicate(timeout=10)
        print(json.dumps({
            'case': name, 'alive': json.loads(alive.stdout),
            'store_request_was_pending': pending, 'store': json.loads(output),
        }), flush=True)
        failed |= alive.returncode != 0 or waiting.returncode != 0 or not pending
    finally:
        safety.cancel()
        held.release(force=True)
        if waiting.poll() is None:
            waiting.terminate()
            waiting.wait(timeout=10)
sys.exit(1 if failed else 0)
