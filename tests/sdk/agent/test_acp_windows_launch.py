"""Native Windows process launches, with an offline npm/ACP fixture."""

import json
import os
import shutil
import sys
import uuid

import pytest

from openhands.sdk.agent.acp_agent import ACPAgent
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.workspace.local import LocalWorkspace


@pytest.mark.skipif(sys.platform != "win32", reason="Windows npm shim regression")
@pytest.mark.parametrize("operation", ["warm_cache", "session"])
async def test_windows_npx_launch_preserves_arguments(tmp_path, monkeypatch, operation):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the offline process fixture")
    npm_dir = tmp_path / "Node installation with spaces"
    cli = npm_dir / "node_modules" / "npm" / "bin" / "npx-cli.js"
    cli.parent.mkdir(parents=True)
    (npm_dir / "npx.cmd").write_text("@exit /b 99\n", encoding="utf-8")
    record = tmp_path / "arguments.json"
    cli.write_text(
        """
const fs = require('node:fs');
const args = process.argv.slice(2);
fs.writeFileSync(process.env.ACP_TEST_ARGUMENTS, JSON.stringify(args));
if (!args.includes('--package')) {
    const lines = require('node:readline').createInterface({ input: process.stdin });
    lines.on('line', line => {
        const request = JSON.parse(line);
        if (request.id === undefined) return;
        const result = request.method === 'initialize'
            ? { protocolVersion: 1, agentCapabilities: {}, authMethods: [] }
            : { sessionId: 'native-windows-session' };
        const response = { jsonrpc: '2.0', id: request.id, result };
        process.stdout.write(JSON.stringify(response) + '\\n');
    });
}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("PATH", str(npm_dir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("ACP_TEST_ARGUMENTS", str(record))
    literal_argument = "literal & value with spaces"
    agent = ACPAgent(acp_command=["npx", "fixture-acp@1.0.0", literal_argument])
    try:
        if operation == "warm_cache":
            await agent._warm_npx_cache(
                [literal_argument], "fixture", dict(os.environ), str(tmp_path)
            )
        else:
            state = ConversationState.create(
                id=uuid.uuid4(),
                agent=agent,
                workspace=LocalWorkspace(working_dir=str(tmp_path)),
            )
            agent.init_state(state, on_event=lambda _: None)
            assert state.agent_state["acp_session_id"] == "native-windows-session"
        assert literal_argument in json.loads(record.read_text(encoding="utf-8"))
    finally:
        agent.close()
