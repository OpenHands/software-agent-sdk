# Custom Tools with Remote Agent Server

This example demonstrates how to use custom tools with a remote agent server by building a custom base image that includes your tool implementations.

## Overview

When using a remote agent server, custom tools must be available in the server's Python environment. This example shows the complete workflow for:

1. **Defining custom tools** that log structured data to a JSON file
2. **Building a custom base image** that includes your tools
3. **Using `DockerDevWorkspace`** to build the agent server on top of the custom base image
4. **Using dynamic tool registration** to make tools available at runtime
5. **Verifying the results** by reading the logged data back from the workspace

## Use Cases

This pattern is useful for:

- **Structured data collection**: Define tools like `log_data`, `record_metric`, or `track_event` to collect structured data during agent runs
- **Custom integrations**: Tools that interact with external systems (APIs, databases, etc.)
- **Domain-specific operations**: Business logic tools specific to your application
- **Downstream processing**: Collected data can be used to generate reports, trigger workflows, etc.

## Architecture

```
┌─────────────────┐         ┌──────────────────────────┐
│   SDK Client    │         │   Remote Agent Server    │
│                 │         │   (Built on custom base) │
│  - Define tools │◄────────┤                          │
│  - Send tasks   │   API   │  - Custom tools in       │
│  - Get results  │         │    Python path           │
│                 │         │  - Dynamic registration  │
└─────────────────┘         │  - Tool execution        │
                            │  - JSON file output      │
                            └──────────────────────────┘
```

## Files in This Example

- **`custom_tools/log_data.py`**: Example custom tool for logging structured data to JSON
- **`Dockerfile`**: Simple Dockerfile that copies custom tools into the base image
- **`build_custom_image.sh`**: Script to build the custom base image
- **`custom_tool_example.py`**: SDK script demonstrating the full workflow
- **`README.md`**: This documentation

## The Custom Tool

The example includes a `LogDataTool` that logs structured data to a JSON file:

```python
# Define the action (input to the tool)
class LogDataAction(Action):
    message: str  # The log message
    level: LogLevel  # Enum: debug, info, warning, error
    data: dict[str, Any]  # Additional structured data

# Define the observation (output from the tool)
class LogDataObservation(Observation):
    success: bool
    log_file: str
    entry_count: int

# Auto-register the tool when module is imported
register_tool("LogDataTool", LogDataTool)
```

## How It Works

### 1. Tool Implementation (`custom_tools/log_data.py`)

The tool defines:
- **Action**: Input structure (what the LLM provides)
- **Observation**: Output structure (what the LLM receives back)
- **Executor**: Logic that writes to `/tmp/agent_data.json`
- **Auto-registration**: `register_tool()` call at module level

### 2. Dockerfile

The Dockerfile is very simple:
```dockerfile
FROM nikolaik/python-nodejs:python3.12-nodejs22

# Copy custom tools into the Python path
COPY custom_tools /app/custom_tools

# Add /app to PYTHONPATH so custom_tools can be imported
ENV PYTHONPATH="/app:${PYTHONPATH}"
```

This creates a base image with your custom tools. The agent server is built on top of this image automatically by `DockerDevWorkspace`.

### 3. Dynamic Tool Registration

When creating a conversation, the SDK:
1. Collects tool module qualnames from the client's registry
2. Sends them to the server in the conversation creation request
3. Server imports those modules, triggering auto-registration
4. Tools become available for agent execution

### 4. SDK Script (`custom_tool_example.py`)

The script:
- Builds the custom base image (if not already built)
- Uses `DockerDevWorkspace` with `base_image` to build the agent server on top
- Creates an agent with the custom tool specified
- Sends a task that uses the custom tool
- Agent executes on the remote server with access to the custom tool
- **Reads the JSON log file back** to verify the tool worked

## Running the Example

### Prerequisites

- Docker installed and running
- OpenHands SDK installed
- `LLM_API_KEY` environment variable set

### Steps

1. **Navigate to this directory**:
   ```bash
   cd examples/02_remote_agent_server/05_custom_tool
   ```

2. **Run the example**:
   ```bash
   python custom_tool_example.py
   ```

The script will:
- Build the custom base image (first run only)
- Build the agent server on top of the base image (first run may take a few minutes)
- Start the agent server with custom tools
- Execute the task using the custom tool
- Read and display the logged data from the JSON file

### Expected Output

```
🔍 Checking for custom base image: custom-base-image:latest
📦 Building custom base image with custom tools...
✅ Custom base image built successfully!
🚀 Building and starting agent server with custom tools...
📋 Conversation ID: <id>
📝 Sending task to analyze files and log findings...
🚀 Running conversation...
✅ Task completed!
📊 Logged Data Summary:
================================================================================
Found 3 log entries:

Entry 1:
  Timestamp: 2024-01-15T10:30:00.000000+00:00
  Level: info
  Message: Starting analysis of Python files
  Data: {"directory": "/workspace"}

Entry 2:
  Timestamp: 2024-01-15T10:30:05.000000+00:00
  Level: info
  Message: Found interesting pattern
  Data: {"file": "example.py", "pattern": "decorator usage"}

Entry 3:
  Timestamp: 2024-01-15T10:30:10.000000+00:00
  Level: warning
  Message: Potential issue detected
  Data: {"file": "utils.py", "line": 42, "issue": "missing error handling"}

================================================================================
✅ Example completed successfully!
```

## Creating Your Own Custom Tools

### 1. Define Your Tool

Create a new Python file in `custom_tools/`:

```python
from openhands.sdk import Action, Observation, ToolDefinition
from openhands.sdk.tool import ToolExecutor, register_tool

class MyAction(Action):
    # Define your input fields
    param1: str
    param2: int

class MyObservation(Observation):
    # Define your output fields
    result: str
    success: bool

class MyExecutor(ToolExecutor[MyAction, MyObservation]):
    def __call__(self, action: MyAction, conversation=None):
        # Implement your tool logic
        return MyObservation(result="...", success=True)

class MyTool(ToolDefinition[MyAction, MyObservation]):
    @classmethod
    def create(cls, conv_state, **params):
        executor = MyExecutor()
        return [cls(
            description="Tool description",
            action_type=MyAction,
            observation_type=MyObservation,
            executor=executor,
        )]

# Auto-register
register_tool("MyTool", MyTool)
```

### 2. Update the Dockerfile

No changes needed! The Dockerfile already copies all of `custom_tools/`.

### 3. Use Your Tool

In your SDK script:

```python
from openhands.workspace import DockerDevWorkspace

# Use DockerDevWorkspace with your custom base image
with DockerDevWorkspace(
    base_image="custom-base-image:latest",
    host_port=8010,
) as workspace:
    # Create agent with your custom tool
    tools = get_default_tools(enable_browser=False)
    tools.append(Tool(name="MyTool"))
    
    agent = Agent(llm=llm, tools=tools, ...)
    # ... rest of your code
```

## Related Documentation

- [Standalone Custom Tools Example](../../01_standalone_sdk/02_custom_tools.py)
- [Tool Definition API](../../../openhands-sdk/openhands/sdk/tool/)
- [Agent Server API](../../../openhands-agent-server/)
- [Dynamic Tool Registration](https://github.com/OpenHands/software-agent-sdk/pull/1129)

## Questions?

If you have questions or run into issues:
1. Check the [SDK documentation](https://docs.all-hands.dev/sdk/)
2. Review existing tools in `openhands-tools/`
3. Open an issue on [GitHub](https://github.com/OpenHands/software-agent-sdk/issues)
