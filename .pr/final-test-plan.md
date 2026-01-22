# PR #1651 Final Manual Test Plan

## Setup

```bash
git clone https://github.com/OpenHands/software-agent-sdk.git sdk-test
cd sdk-test
git fetch origin feat/agent-server-plugin-loading
git checkout feat/agent-server-plugin-loading
uv sync
```

---

## Test Cases

### 1. SDK LocalConversation - Local Path

```bash
uv run python -c "
import tempfile
from pathlib import Path
from pydantic import SecretStr
from openhands.sdk import LLM, Agent
from openhands.sdk.plugin import PluginSource
from openhands.sdk.conversation.impl.local_conversation import LocalConversation

llm = LLM(usage_id='test', model='test/model', api_key=SecretStr('fake'))
agent = Agent(llm=llm, tools=[])

# Use built-in example plugin
plugin_path = Path('examples/05_skills_and_plugins/02_loading_plugins/example_plugins/code-quality')

with tempfile.TemporaryDirectory() as tmpdir:
    conv = LocalConversation(
        agent=agent,
        workspace=tmpdir,
        plugins=[PluginSource(source=str(plugin_path))],
        visualizer=None,
    )
    conv._ensure_plugins_loaded()
    skills = [s.name for s in conv.agent.agent_context.skills]
    print(f'✅ Test 1 PASSED - Skills: {skills}')
    conv.close()
"
```

### 2. SDK LocalConversation - GitHub Reference

```bash
uv run python -c "
import tempfile
from pydantic import SecretStr
from openhands.sdk import LLM, Agent
from openhands.sdk.plugin import PluginSource
from openhands.sdk.conversation.impl.local_conversation import LocalConversation

llm = LLM(usage_id='test', model='test/model', api_key=SecretStr('fake'))
agent = Agent(llm=llm, tools=[])

with tempfile.TemporaryDirectory() as tmpdir:
    conv = LocalConversation(
        agent=agent,
        workspace=tmpdir,
        plugins=[PluginSource(
            source='github:jpshackelford/openhands-sample-plugins',
            repo_path='plugins/magic-test',
        )],
        visualizer=None,
    )
    conv._ensure_plugins_loaded()
    ref = conv.resolved_plugins[0].resolved_ref
    skills = [s.name for s in conv.agent.agent_context.skills]
    print(f'✅ Test 2 PASSED - Ref: {ref[:8]}, Skills: {skills}')
    conv.close()
"
```

### 3. Agent Server - Local Path

```bash
uv run python -c "
import tempfile
from pathlib import Path
from pydantic import SecretStr
from openhands.sdk import LLM, Agent
from openhands.sdk.plugin import PluginSource
from openhands.sdk.plugin.loader import load_plugins
from openhands.agent_server.models import StartConversationRequest
from openhands.sdk.workspace import LocalWorkspace

llm = LLM(usage_id='test', model='test/model', api_key=SecretStr('fake'))
agent = Agent(llm=llm, tools=[])
plugin_path = Path('examples/05_skills_and_plugins/02_loading_plugins/example_plugins/code-quality')

with tempfile.TemporaryDirectory() as tmpdir:
    request = StartConversationRequest(
        agent=agent,
        workspace=LocalWorkspace(working_dir=tmpdir),
        plugins=[PluginSource(source=str(plugin_path))],
    )
    updated_agent, hooks = load_plugins(request.plugins, request.agent)
    skills = [s.name for s in updated_agent.agent_context.skills]
    print(f'✅ Test 3 PASSED - Skills: {skills}')
"
```

### 4. Agent Server - GitHub Reference

```bash
uv run python -c "
import tempfile
from pydantic import SecretStr
from openhands.sdk import LLM, Agent
from openhands.sdk.plugin import PluginSource
from openhands.sdk.plugin.loader import load_plugins
from openhands.agent_server.models import StartConversationRequest
from openhands.sdk.workspace import LocalWorkspace

llm = LLM(usage_id='test', model='test/model', api_key=SecretStr('fake'))
agent = Agent(llm=llm, tools=[])

with tempfile.TemporaryDirectory() as tmpdir:
    request = StartConversationRequest(
        agent=agent,
        workspace=LocalWorkspace(working_dir=tmpdir),
        plugins=[PluginSource(
            source='github:jpshackelford/openhands-sample-plugins',
            repo_path='plugins/magic-test',
        )],
    )
    updated_agent, hooks = load_plugins(request.plugins, request.agent)
    skills = [s.name for s in updated_agent.agent_context.skills]
    print(f'✅ Test 4 PASSED - Skills: {skills}')
"
```

### 5. Docker Image - Local Path

```bash
# Update SHA from PR description
IMAGE="ghcr.io/openhands/agent-server:606649e-python"
docker pull $IMAGE

# Start server with local plugin mounted
docker run -d --rm -p 8000:8000 --name agent-server-test \
  -v $(pwd)/examples/05_skills_and_plugins/02_loading_plugins/example_plugins:/plugins \
  $IMAGE
sleep 8

# Call API with local path plugin
curl -s -X POST http://localhost:8000/api/conversations \
  -H "Content-Type: application/json" \
  -d '{
    "agent": {"llm": {"model": "test/model", "api_key": "fake", "usage_id": "test"}, "tools": []},
    "workspace": {"working_dir": "/workspace", "kind": "LocalWorkspace"},
    "plugins": [{"source": "/plugins/code-quality"}]
  }'

docker stop agent-server-test
echo "✅ Test 5 PASSED if conversation ID returned"
```

### 6. Docker Image - GitHub Reference

```bash
IMAGE="ghcr.io/openhands/agent-server:606649e-python"

# Start server
docker run -d --rm -p 8000:8000 --name agent-server-test $IMAGE
sleep 8

# Call API with GitHub plugin reference
curl -s -X POST http://localhost:8000/api/conversations \
  -H "Content-Type: application/json" \
  -d '{
    "agent": {"llm": {"model": "test/model", "api_key": "fake", "usage_id": "test"}, "tools": []},
    "workspace": {"working_dir": "/workspace", "kind": "LocalWorkspace"},
    "plugins": [{"source": "github:jpshackelford/openhands-sample-plugins", "repo_path": "plugins/magic-test"}]
  }'

docker stop agent-server-test
echo "✅ Test 6 PASSED if conversation ID returned"
```

### 7. Hooks - Plugin + Explicit Combined

```bash
uv run python -c "
import json, tempfile
from pathlib import Path
from pydantic import SecretStr
from openhands.sdk import LLM, Agent
from openhands.sdk.plugin import PluginSource
from openhands.sdk.hooks import HookConfig
from openhands.sdk.hooks.config import HookMatcher, HookDefinition
from openhands.sdk.conversation.impl.local_conversation import LocalConversation

llm = LLM(usage_id='test', model='test/model', api_key=SecretStr('fake'))
agent = Agent(llm=llm, tools=[])

with tempfile.TemporaryDirectory() as tmpdir:
    # Create plugin with hooks
    plugin_dir = Path(tmpdir) / 'hook-plugin'
    plugin_dir.mkdir()
    (plugin_dir / '.plugin').mkdir()
    (plugin_dir / '.plugin' / 'plugin.json').write_text('{\"name\": \"hook-plugin\", \"version\": \"1.0.0\"}')
    (plugin_dir / 'hooks').mkdir()
    (plugin_dir / 'hooks' / 'hooks.json').write_text(json.dumps({
        'hooks': {'PreToolUse': [{'matcher': '*', 'hooks': [{'command': 'echo plugin'}]}]}
    }))

    workspace = Path(tmpdir) / 'workspace'
    workspace.mkdir()

    explicit_hooks = HookConfig(pre_tool_use=[
        HookMatcher(matcher='*', hooks=[HookDefinition(command='echo explicit')])
    ])

    conv = LocalConversation(
        agent=agent,
        workspace=str(workspace),
        plugins=[PluginSource(source=str(plugin_dir))],
        hook_config=explicit_hooks,
        visualizer=None,
    )
    conv._ensure_plugins_loaded()
    assert conv._hook_processor is not None
    print('✅ Test 7 PASSED - Hook processor created with combined hooks')
    conv.close()
"
```

### 8. MCP Tools - Plugin MCP Config Initialized

```bash
uv run python -c "
import tempfile
from pathlib import Path
from pydantic import SecretStr
from openhands.sdk import LLM, Agent
from openhands.sdk.plugin import PluginSource
from openhands.sdk.conversation.impl.local_conversation import LocalConversation

llm = LLM(usage_id='test', model='test/model', api_key=SecretStr('fake'))
agent = Agent(llm=llm, tools=[])

# Use example plugin that has MCP config
plugin_path = Path('examples/05_skills_and_plugins/02_loading_plugins/example_plugins/code-quality')

with tempfile.TemporaryDirectory() as tmpdir:
    conv = LocalConversation(
        agent=agent,
        workspace=tmpdir,
        plugins=[PluginSource(source=str(plugin_path))],
        visualizer=None,
    )
    # Before: no MCP config
    assert conv.agent.mcp_config is None or conv.agent.mcp_config == {}
    
    conv._ensure_plugins_loaded()
    
    # After: MCP config merged from plugin
    assert conv.agent.mcp_config is not None
    assert 'mcpServers' in conv.agent.mcp_config
    servers = list(conv.agent.mcp_config['mcpServers'].keys())
    print(f'✅ Test 8 PASSED - MCP servers: {servers}')
    conv.close()
"
```

---

## Quick Run All

```bash
# Run tests 1-4, 7-8 (SDK tests)
for i in 1 2 3 4 7 8; do
  echo "--- Running Test $i ---"
  # Copy/paste the test command here
done

# Docker tests (5-6) require manual docker setup
```

---

## Expected Results

| Test | What to Check |
|------|---------------|
| 1 | Skills loaded from local plugin |
| 2 | GitHub plugin cloned, commit SHA resolved |
| 3 | StartConversationRequest accepts plugins list |
| 4 | load_plugins() works with GitHub source |
| 5 | API accepts local path plugin, conversation ID returned |
| 6 | API accepts GitHub plugin reference, conversation ID returned |
| 7 | Hook processor created with both hook sources |
| 8 | MCP config merged from plugin |
