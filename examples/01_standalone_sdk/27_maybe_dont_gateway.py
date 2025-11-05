"""Example: Maybe Don't Security Gateway Integration.

This example demonstrates how to integrate the Maybe Don't security gateway
with OpenHands Agent SDK using the Model Context Protocol (MCP).

Prerequisites:
1. Install Maybe Don't from https://www.maybedont.ai/download/
2. Configure gateway-config.yaml (see examples/maybe_dont/gateway-config.yaml)
3. Start Maybe Don't gateway: `maybe-dont --config gateway-config.yaml`
4. Set required environment variables (see below)

The Maybe Don't gateway acts as an MCP proxy that:
- Intercepts all tool calls before execution
- Applies CEL-based and AI-powered security policies
- Allows, denies, or flags operations based on risk
- Logs all actions for audit and compliance

Architecture:
    OpenHands Agent (MCP Client)
           ↓
    Maybe Don't Gateway (MCP Middleware)
           ↓
    Downstream MCP Servers (Tools: filesystem, fetch, etc.)
"""

import os
import time

from pydantic import SecretStr

from openhands.sdk import (
    LLM,
    Agent,
    Conversation,
    Event,
    LLMConvertibleEvent,
    get_logger,
)
from openhands.sdk.security.llm_analyzer import LLMSecurityAnalyzer
from openhands.sdk.tool import Tool
from openhands.tools.execute_bash import BashTool
from openhands.tools.file_editor import FileEditorTool


logger = get_logger(__name__)

# ==============================================================================
# Configuration
# ==============================================================================

# LLM Configuration
api_key = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."
model = os.getenv("LLM_MODEL", "openhands/claude-sonnet-4-5-20250929")
base_url = os.getenv("LLM_BASE_URL")

llm = LLM(
    usage_id="agent",
    model=model,
    base_url=base_url,
    api_key=SecretStr(api_key),
)

# Workspace configuration
cwd = os.getcwd()

# Built-in OpenHands tools
tools = [
    Tool(name=BashTool.name),
    Tool(name=FileEditorTool.name),
]

# ==============================================================================
# Maybe Don't Gateway Configuration
# ==============================================================================

# Check if Maybe Don't is enabled
maybe_dont_enabled = os.getenv("MAYBE_DONT_ENABLED", "false").lower() == "true"

if maybe_dont_enabled:
    logger.info("=" * 80)
    logger.info("Maybe Don't Security Gateway Integration Enabled")
    logger.info("=" * 80)

    # MCP Configuration pointing to Maybe Don't gateway
    # The gateway should be running and listening on the configured address
    mcp_config = {
        "mcpServers": {
            # Connect to Maybe Don't gateway
            # The gateway will proxy to downstream MCP servers
            "secured-tools": {
                "url": os.getenv("MAYBE_DONT_URL", "http://127.0.0.1:8080"),
                "transport": os.getenv("MAYBE_DONT_TRANSPORT", "sse"),
                # Optional: Authentication if gateway requires it
                # "headers": {
                #     "Authorization": f"Bearer {os.getenv('MAYBE_DONT_TOKEN', '')}"
                # }
            }
        }
    }

    logger.info(f"Gateway URL: {mcp_config['mcpServers']['secured-tools']['url']}")
    logger.info(f"Transport: {mcp_config['mcpServers']['secured-tools']['transport']}")
else:
    logger.warning("=" * 80)
    logger.warning("Maybe Don't Gateway is DISABLED")
    logger.warning("Set MAYBE_DONT_ENABLED=true to enable security gateway")
    logger.warning("=" * 80)
    mcp_config = None

# ==============================================================================
# Agent Initialization
# ==============================================================================

# Create agent with optional MCP gateway configuration
agent = Agent(
    llm=llm,
    tools=tools,
    mcp_config=mcp_config,
    # LLM Security Analyzer provides additional layer of security
    # by having the LLM predict risk levels before execution
    security_analyzer=LLMSecurityAnalyzer(),
)

# ==============================================================================
# Conversation Setup
# ==============================================================================

llm_messages = []  # Collect raw LLM messages for analysis


def conversation_callback(event: Event):
    """Callback to collect LLM messages."""
    if isinstance(event, LLMConvertibleEvent):
        llm_messages.append(event.to_llm_message())


# Create conversation
conversation = Conversation(
    agent=agent,
    callbacks=[conversation_callback],
    workspace=cwd,
)

# ==============================================================================
# Example Scenarios
# ==============================================================================

def run_example(title: str, message: str, description: str):
    """Run an example scenario and log the results."""
    logger.info("\n" + "=" * 80)
    logger.info(f"EXAMPLE: {title}")
    logger.info("=" * 80)
    logger.info(f"Description: {description}")
    logger.info(f"User message: {message}")
    logger.info("-" * 80)

    try:
        conversation.send_message(message)
        conversation.run()
        logger.info("✓ Execution completed")
    except Exception as e:
        logger.error(f"✗ Execution failed: {e}")

    # Brief pause between examples
    time.sleep(1)


# Example 1: Safe Read Operation
# Expected: ALLOWED by Maybe Don't
run_example(
    title="Safe File Read",
    message="Please read the README.md file and summarize what it says.",
    description=(
        "Reading a file is generally safe. "
        "Maybe Don't should ALLOW this operation."
    ),
)

# Example 2: File Creation
# Expected: ALLOWED (if in workspace) or FLAGGED (depends on policies)
run_example(
    title="File Creation",
    message=(
        "Create a new file called 'test-output.txt' in the current directory "
        "with the text 'Hello from OpenHands with Maybe Don't security!'"
    ),
    description=(
        "Creating a file in the workspace is typically allowed. "
        "Maybe Don't may FLAG this for audit purposes."
    ),
)

# Example 3: Web Fetch (if fetch server configured)
# Expected: ALLOWED or FLAGGED depending on URL
if maybe_dont_enabled:
    run_example(
        title="Web Fetch Operation",
        message=(
            "Fetch the content from https://www.example.com and tell me "
            "what you find."
        ),
        description=(
            "Web fetching depends on the URL. Public sites like example.com "
            "should be allowed. Internal or admin URLs may be denied."
        ),
    )

# Example 4: Potentially Risky Operation
# Expected: FLAGGED or DENIED depending on policies
run_example(
    title="Bulk File Operation",
    message=(
        "List all .txt files in the current directory and show me their sizes."
    ),
    description=(
        "Listing files is generally safe, but bulk operations may be flagged "
        "by AI validation if they appear suspicious."
    ),
)

# Example 5: High-Risk Operation
# Expected: DENIED by Maybe Don't
run_example(
    title="System Modification Attempt",
    message=(
        "Check if we have write access to /etc/hosts and if so, "
        "show me the current contents."
    ),
    description=(
        "Accessing system directories like /etc should be DENIED by "
        "CEL rules or AI validation. This demonstrates security enforcement."
    ),
)

# Example 6: Mass Deletion Pattern
# Expected: DENIED by Maybe Don't
run_example(
    title="Dangerous Deletion Pattern",
    message="Delete all temporary files matching *.tmp in the workspace.",
    description=(
        "Mass deletion operations should be caught by both CEL rules "
        "and AI validation, even if limited to workspace."
    ),
)

# ==============================================================================
# Summary and Analysis
# ==============================================================================

logger.info("\n" + "=" * 80)
logger.info("Example Execution Complete")
logger.info("=" * 80)

if maybe_dont_enabled:
    logger.info(
        "\nMaybe Don't Gateway Results:\n"
        "- Check the gateway logs for detailed security decisions\n"
        "- Review audit log (maybe-dont-audit.log) for all operations\n"
        "- Look for ALLOW, DENY, and FLAG decisions with reasons"
    )
else:
    logger.warning(
        "\nMaybe Don't was DISABLED for this run.\n"
        "To test with security gateway:\n"
        "1. Install and configure Maybe Don't\n"
        "2. Start gateway: maybe-dont --config gateway-config.yaml\n"
        "3. Set MAYBE_DONT_ENABLED=true\n"
        "4. Run this example again"
    )

# ==============================================================================
# Key Integration Points
# ==============================================================================

logger.info(
    "\n" + "=" * 80 + "\n"
    "How This Integration Works:\n"
    "=" * 80 + "\n"
    "\n"
    "1. Architecture:\n"
    "   - OpenHands Agent acts as an MCP client\n"
    "   - Maybe Don't runs as a separate MCP gateway/proxy\n"
    "   - Downstream MCP servers provide actual tool functionality\n"
    "\n"
    "2. Configuration:\n"
    "   - mcp_config points to Maybe Don't gateway URL\n"
    "   - gateway-config.yaml defines security policies\n"
    "   - No SDK code changes required\n"
    "\n"
    "3. Security Flow:\n"
    "   - Agent calls tool via MCP protocol\n"
    "   - Maybe Don't intercepts the call\n"
    "   - CEL rules check for known dangerous patterns\n"
    "   - AI validation analyzes context and intent\n"
    "   - Gateway decides: ALLOW, DENY, or FLAG\n"
    "   - If allowed, request proxies to downstream server\n"
    "   - Results return through gateway to agent\n"
    "\n"
    "4. Benefits:\n"
    "   - Transparent security enforcement\n"
    "   - No changes to OpenHands SDK code\n"
    "   - Centralized security policies\n"
    "   - Comprehensive audit logging\n"
    "   - Works with all MCP tools\n"
    "\n"
    "5. Defense in Depth:\n"
    "   - LLMSecurityAnalyzer: LLM predicts risks before calling tools\n"
    "   - Maybe Don't CEL: Fast deterministic policy checks\n"
    "   - Maybe Don't AI: Contextual threat analysis\n"
    "   - Tool-level validation: Built-in safety in tools themselves\n"
)

# ==============================================================================
# Configuration Reference
# ==============================================================================

logger.info(
    "\n" + "=" * 80 + "\n"
    "Environment Variables:\n"
    "=" * 80 + "\n"
    "\n"
    "Required:\n"
    "  LLM_API_KEY         - API key for the LLM provider\n"
    "  OPENAI_API_KEY      - API key for Maybe Don't AI validation\n"
    "\n"
    "Optional:\n"
    "  MAYBE_DONT_ENABLED  - Set to 'true' to enable gateway (default: false)\n"
    "  MAYBE_DONT_URL      - Gateway URL (default: http://127.0.0.1:8080)\n"
    "  MAYBE_DONT_TRANSPORT - MCP transport type (default: sse, options: http/sse)\n"
    "  LLM_MODEL           - LLM model to use (default: claude-sonnet-4-5)\n"
    "  LLM_BASE_URL        - Custom LLM API base URL\n"
    "\n"
    "Gateway Setup:\n"
    "  1. Copy examples/maybe_dont/gateway-config.yaml to working directory\n"
    "  2. Customize security policies as needed\n"
    "  3. Set OPENAI_API_KEY environment variable\n"
    "  4. Start gateway: maybe-dont --config gateway-config.yaml\n"
    "  5. Verify gateway is running: curl http://127.0.0.1:8080/health\n"
    "\n"
    "Documentation:\n"
    "  See docs/MAYBE_DONT_GATEWAY.md for complete integration guide\n"
)

# Report cost
cost = llm.metrics.accumulated_cost
logger.info(f"\nTotal LLM cost: ${cost:.4f}")
