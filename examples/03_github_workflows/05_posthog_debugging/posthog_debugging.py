#!/usr/bin/env python3
"""
PostHog Debugging Example

This example demonstrates how to use the OpenHands agent to debug errors
logged in PostHog.
The agent will:
1. Query PostHog events to understand the error using the Query API
2. Clone relevant GitHub repositories using git commands
3. Analyze the codebase to identify potential causes
4. Attempt to reproduce the error
5. Optionally create a draft PR with a fix

Usage:
    python posthog_debugging.py --query "$exception" \\
        --repos "All-Hands-AI/OpenHands,All-Hands-AI/deploy"

Environment Variables Required:
    - POSTHOG_API_KEY: Your PostHog Personal API key
    - POSTHOG_PROJECT_ID: Your PostHog project ID
    - POSTHOG_HOST: (optional) PostHog host (e.g., us.i.posthog.com, eu.i.posthog.com)
    - GITHUB_TOKEN: Your GitHub personal access token
    - LLM_API_KEY: API key for the LLM service
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests
from jinja2 import Environment, FileSystemLoader
from pydantic import SecretStr

from openhands.sdk import (
    LLM,
    Agent,
    Conversation,
    Event,
    LLMConvertibleEvent,
    Message,
    TextContent,
    get_logger,
)
from openhands.sdk.tool import Tool, register_tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.task_tracker import TaskTrackerTool
from openhands.tools.terminal import TerminalTool


logger = get_logger(__name__)


def validate_environment():
    """Validate that all required environment variables are set."""
    required_vars = [
        "POSTHOG_API_KEY",
        "POSTHOG_PROJECT_ID",
        "GITHUB_TOKEN",
        "LLM_API_KEY",
    ]

    missing_vars = []
    for var in required_vars:
        if not os.getenv(var):
            missing_vars.append(var)

    if missing_vars:
        print(f"❌ Missing required environment variables: {', '.join(missing_vars)}")
        print("\nPlease set the following environment variables:")
        for var in missing_vars:
            print(f"  export {var}=your_key_here")
        return False

    return True


def fetch_posthog_events(
    query: str, working_dir: Path, query_type: str = "event-query", limit: int = 5
) -> Path:
    """
    Fetch event examples from PostHog and save to a JSON file.

    Args:
        query: PostHog query string (event name or event ID)
        working_dir: Directory to save the event examples
        query_type: Type of query - "event-query" (uses Query API with HogQL) or
            "event-id" (fetches specific event)
        limit: Maximum number of event examples to fetch (default: 5)

    Returns:
        Path to the JSON file containing event examples
    """
    posthog_api_key = os.getenv("POSTHOG_API_KEY")
    posthog_project_id = os.getenv("POSTHOG_PROJECT_ID")
    posthog_host = os.getenv("POSTHOG_HOST", "us.i.posthog.com")

    event_examples = []

    if query_type == "event-id":
        # Fetch specific event by ID
        api_url = (
            f"https://{posthog_host}/api/projects/{posthog_project_id}/events/{query}"
        )

        print("📡 Fetching specific event from PostHog...")
        print(f"   Event ID: {query}")
        print(f"   API: {api_url}")

        headers = {
            "Authorization": f"Bearer {posthog_api_key}",
        }

        try:
            response = requests.get(api_url, headers=headers, timeout=30)
            response.raise_for_status()
        except requests.exceptions.Timeout:
            print("❌ Error: Request to PostHog API timed out")
            sys.exit(1)
        except requests.exceptions.RequestException as e:
            print(f"❌ Error fetching from PostHog API: {e}")
            sys.exit(1)

        try:
            event_data = response.json()
        except json.JSONDecodeError as e:
            print(f"❌ Error parsing PostHog API response: {e}")
            print(f"   Response: {response.text[:500]}")
            sys.exit(1)

        # Extract event details
        event_example = {
            "example_number": 1,
            "event_id": event_data.get("id"),
            "event": event_data.get("event"),
            "distinct_id": event_data.get("distinct_id"),
            "properties": event_data.get("properties", {}),
            "timestamp": event_data.get("timestamp"),
            "person": event_data.get("person"),
        }
        event_examples.append(event_example)

    else:  # event-query
        # Use Query API with HogQL to fetch events
        api_url = f"https://{posthog_host}/api/projects/{posthog_project_id}/query/"

        # Build HogQL query to fetch events
        # Query for events in the last 30 days
        hogql_query = (
            f"SELECT * FROM events WHERE event = '{query}' "
            f"ORDER BY timestamp DESC LIMIT {limit}"
        )

        request_body = {
            "query": {"kind": "HogQLQuery", "query": hogql_query},
            "refresh": "blocking",
        }

        print(f"📡 Fetching up to {limit} events from PostHog...")
        print(f"   Event name: {query}")
        print(f"   API: {api_url}")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {posthog_api_key}",
        }

        try:
            response = requests.post(
                api_url, headers=headers, json=request_body, timeout=30
            )
            response.raise_for_status()
        except requests.exceptions.Timeout:
            print("❌ Error: Request to PostHog API timed out")
            sys.exit(1)
        except requests.exceptions.RequestException as e:
            print(f"❌ Error fetching from PostHog API: {e}")
            if hasattr(e, "response") and e.response:
                print(f"   Response: {e.response.text[:500]}")
            sys.exit(1)

        try:
            response_data = response.json()
        except json.JSONDecodeError as e:
            print(f"❌ Error parsing PostHog API response: {e}")
            print(f"   Response: {response.text[:500]}")
            sys.exit(1)

        # Check for API errors
        if "error" in response_data:
            print(f"❌ PostHog API error: {response_data['error']}")
            sys.exit(1)

        # Extract event results from HogQL query response
        results = response_data.get("results", [])

        if results:
            # The results are in a columnar format, need to parse them
            columns = response_data.get("columns", [])
            rows = response_data.get("results", [])

            for idx, row in enumerate(rows[:limit], 1):
                # Create a dictionary mapping column names to values
                event_dict = {}
                if columns:
                    for col_idx, col_name in enumerate(columns):
                        if col_idx < len(row):
                            event_dict[col_name] = row[col_idx]
                else:
                    # Fallback if no columns provided
                    event_dict = {"data": row}

                event_example = {
                    "example_number": idx,
                    "event_id": event_dict.get("uuid") or event_dict.get("id"),
                    "event": event_dict.get("event"),
                    "distinct_id": event_dict.get("distinct_id"),
                    "properties": event_dict.get("properties", {}),
                    "timestamp": event_dict.get("timestamp"),
                    "person_id": event_dict.get("person_id"),
                }
                event_examples.append(event_example)

    # Save to file
    events_file = working_dir / "posthog_events.json"
    with open(events_file, "w") as f:
        json.dump(
            {
                "query": query,
                "fetch_time": datetime.now().isoformat(),
                "total_examples": len(event_examples),
                "examples": event_examples,
            },
            f,
            indent=2,
        )

    print(f"✅ Fetched {len(event_examples)} event examples")
    print(f"📄 Saved to: {events_file}")
    return events_file


def create_unique_identifier(query: str, events_data: dict) -> str:
    """
    Create a unique identifier for the event based on query or event ID.

    Args:
        query: The PostHog query string
        events_data: The parsed event data from posthog_events.json

    Returns:
        Unique identifier string
    """
    # Check if we have a specific event ID
    examples = events_data.get("examples", [])
    if examples and examples[0].get("event_id"):
        event_id = examples[0]["event_id"]
        return f"event-id: {event_id}"
    else:
        # Use query as identifier
        return f"query: {query}"


def search_existing_issue(
    issue_repo: str, identifier: str, github_token: str
) -> int | None:
    """
    Search for existing GitHub issues containing the identifier.

    Args:
        issue_repo: Repository in format 'owner/repo'
        identifier: Unique identifier to search for
        github_token: GitHub API token

    Returns:
        Issue number if found, None otherwise
    """
    print(f"🔍 Searching for existing issue with identifier: {identifier}")

    # Search issues in the repository
    search_query = f'repo:{issue_repo} is:issue "{identifier}"'
    url = "https://api.github.com/search/issues"
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
    }
    params = {"q": search_query}

    try:
        response = requests.get(url, headers=headers, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        items = data.get("items", [])
        if items:
            # Sort by created_at to get the oldest issue (first created)
            items_sorted = sorted(items, key=lambda x: x["created_at"])
            issue_number = items_sorted[0]["number"]
            print(f"✅ Found existing issue #{issue_number} (oldest of {len(items)})")
            return issue_number
        else:
            print("❌ No existing issue found")
            return None
    except (
        requests.exceptions.RequestException,
        json.JSONDecodeError,
        KeyError,
    ) as e:
        print(f"⚠️  Error searching for issues: {e}")
        return None


def create_github_issue(
    issue_repo: str,
    title: str,
    body: str,
    github_token: str,
) -> int:
    """
    Create a new GitHub issue.

    Args:
        issue_repo: Repository in format 'owner/repo'
        title: Issue title
        body: Issue body content
        github_token: GitHub API token

    Returns:
        Created issue number
    """
    print(f"📝 Creating new issue: {title}")

    url = f"https://api.github.com/repos/{issue_repo}/issues"

    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
    }
    payload = {"title": title, "body": body}

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"❌ Error creating issue: {e}")
        if hasattr(e, "response") and e.response:
            print(f"Response: {e.response.text[:500]}")
        sys.exit(1)

    try:
        data = response.json()
        issue_number = data["number"]
        issue_url = data["html_url"]
        print(f"✅ Created issue #{issue_number}: {issue_url}")
        return issue_number
    except (json.JSONDecodeError, KeyError) as e:
        print(f"❌ Error parsing response: {e}")
        print(f"Response: {response.text[:500]}")
        sys.exit(1)


def format_issue_body(
    events_data: dict,
    identifier: str,
    parent_issue_url: str | None,
) -> str:
    """
    Format the GitHub issue body with event details.

    Args:
        events_data: The parsed event data
        identifier: Unique identifier
        parent_issue_url: Optional parent issue URL

    Returns:
        Formatted issue body
    """
    examples = events_data.get("examples", [])
    query = events_data.get("query", "")

    body_parts = []

    # Add parent issue reference if provided
    if parent_issue_url:
        body_parts.append(f"**Parent Issue:** {parent_issue_url}\n")

    # Add identifier for searchability
    body_parts.append(f"**Identifier:** `{identifier}`\n")

    # Add query info
    body_parts.append(f"**Query:** `{query}`\n")

    # Add event summary
    if examples:
        first_example = examples[0]
        body_parts.append("## Event Summary\n")

        if first_example.get("event"):
            body_parts.append(f"- **Event Name:** `{first_example['event']}`")
        if first_example.get("distinct_id"):
            body_parts.append(f"- **Distinct ID:** `{first_example['distinct_id']}`")
        if first_example.get("timestamp"):
            body_parts.append(f"- **Timestamp:** {first_example['timestamp']}")

        body_parts.append("")

        # Add properties if available
        properties = first_example.get("properties", {})
        if properties:
            body_parts.append("## Event Properties\n")
            body_parts.append("```json")
            body_parts.append(json.dumps(properties, indent=2))
            body_parts.append("```\n")

    # Add note about full data
    body_parts.append("## Full Event Data\n")
    body_parts.append(
        "The complete event data has been saved and will be analyzed "
        "by the debugging agent.\n"
    )

    # Add JSON data as collapsible section
    body_parts.append("<details>")
    body_parts.append("<summary>View Full Event Data (JSON)</summary>\n")
    body_parts.append("```json")
    body_parts.append(json.dumps(events_data, indent=2))
    body_parts.append("```")
    body_parts.append("</details>\n")

    body_parts.append("---")
    body_parts.append(
        "*This issue is being tracked by an automated debugging agent. "
        "Analysis findings will be posted as comments below.*"
    )

    return "\n".join(body_parts)


def setup_github_issue(
    query: str,
    events_file: Path,
    issue_repo: str,
    issue_prefix: str,
    issue_parent: str | None,
) -> tuple[int, str]:
    """
    Create or find GitHub issue for tracking debugging progress.

    Args:
        query: The PostHog query
        events_file: Path to the events JSON file
        issue_repo: GitHub repository for issues
        issue_prefix: Prefix for issue titles
        issue_parent: Optional parent issue URL

    Returns:
        Tuple of (issue_number, issue_url)
    """
    github_token = os.getenv("GITHUB_TOKEN")
    if not github_token:
        print("❌ GITHUB_TOKEN environment variable not set")
        sys.exit(1)

    # Load event data
    with open(events_file) as f:
        events_data = json.load(f)

    # Create unique identifier
    identifier = create_unique_identifier(query, events_data)

    # Search for existing issue
    issue_number = search_existing_issue(issue_repo, identifier, github_token)

    if issue_number:
        # Return existing issue
        issue_url = f"https://github.com/{issue_repo}/issues/{issue_number}"
        return issue_number, issue_url

    # Create new issue
    # Determine title from event data
    examples = events_data.get("examples", [])
    if examples and examples[0].get("event"):
        event_name = examples[0]["event"]
    else:
        # Use query as fallback
        event_name = query[:50]

    title = f"{issue_prefix}{event_name}"

    # Format issue body
    body = format_issue_body(events_data, identifier, issue_parent)

    # Create issue
    issue_number = create_github_issue(issue_repo, title, body, github_token)
    issue_url = f"https://github.com/{issue_repo}/issues/{issue_number}"

    return issue_number, issue_url


def create_debugging_prompt(
    query: str, repos: list[str], events_file: Path, issue_url: str
) -> str:
    """Create the debugging prompt for the agent."""
    repos_list = "\n".join(f"- {repo}" for repo in repos)
    posthog_host = os.getenv("POSTHOG_HOST", "us.i.posthog.com")
    posthog_project_id = os.getenv("POSTHOG_PROJECT_ID")
    query_url = f"https://{posthog_host}/api/projects/{posthog_project_id}/query/"
    events_url = f"https://{posthog_host}/api/projects/{posthog_project_id}/events/"

    # Load Jinja2 template
    template_dir = Path(__file__).parent
    env = Environment(loader=FileSystemLoader(template_dir))
    template = env.get_template("debug_prompt.jinja")

    # Render template with context
    prompt = template.render(
        issue_url=issue_url,
        events_file=events_file,
        query=query,
        query_url=query_url,
        events_url=events_url,
        repos_list=repos_list,
    )

    return prompt


def main():
    """Main function to run the PostHog debugging example."""
    parser = argparse.ArgumentParser(
        description="Debug errors from PostHog events using OpenHands agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--query-type",
        choices=["event-query", "event-id"],
        default="event-query",
        help=(
            "Type of query: 'event-query' for event name queries "
            "(e.g., '$exception'), "
            "'event-id' for specific event ID"
        ),
    )
    parser.add_argument(
        "--query",
        required=True,
        help=(
            "PostHog query string. For 'event-query': event name like "
            "'$exception' or 'error'. For 'event-id': "
            "specific event ID"
        ),
    )
    parser.add_argument(
        "--repos",
        required=True,
        help="Comma-separated list of GitHub repositories to analyze "
        "(e.g., 'All-Hands-AI/OpenHands,All-Hands-AI/deploy')",
    )
    parser.add_argument(
        "--working-dir",
        default="./posthog_debug_workspace",
        help="Working directory for cloning repos and analysis "
        "(default: ./posthog_debug_workspace)",
    )
    parser.add_argument(
        "--issue-repo",
        required=True,
        help="GitHub repository for creating/updating issues "
        "(e.g., 'All-Hands-AI/infra')",
    )
    parser.add_argument(
        "--issue-parent",
        help="Parent issue URL to reference (e.g., "
        "'https://github.com/All-Hands-AI/infra/issues/672')",
    )
    parser.add_argument(
        "--issue-prefix",
        default="",
        help="Prefix to add to issue titles (e.g., 'PostHog Error: ')",
    )

    args = parser.parse_args()

    # Validate environment
    if not validate_environment():
        sys.exit(1)

    # Parse repositories
    repos = [repo.strip() for repo in args.repos.split(",")]

    # Create working directory
    working_dir = Path(args.working_dir).resolve()
    working_dir.mkdir(exist_ok=True)

    print("🔍 Starting PostHog debugging session")
    print(f"📊 Query: {args.query}")
    print(f"📁 Repositories: {', '.join(repos)}")
    print(f"🌍 PostHog host: {os.getenv('POSTHOG_HOST', 'us.i.posthog.com')}")
    print(f"💼 Working directory: {working_dir}")
    print()

    # Fetch event examples from PostHog
    events_file = fetch_posthog_events(args.query, working_dir, args.query_type)
    print()

    # Setup GitHub issue for tracking
    print("📋 Setting up GitHub issue for tracking...")
    issue_number, issue_url = setup_github_issue(
        args.query,
        events_file,
        args.issue_repo,
        args.issue_prefix,
        args.issue_parent,
    )
    print(f"📌 Tracking issue: {issue_url}")
    print()

    # Configure LLM
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        print("❌ LLM_API_KEY environment variable is required")
        sys.exit(1)

    # Get LLM configuration from environment
    model = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929")
    base_url = os.getenv("LLM_BASE_URL")

    llm = LLM(
        model=model,
        base_url=base_url,
        api_key=SecretStr(api_key),
    )

    # Run debugging session
    run_debugging_session(llm, working_dir, args.query, repos, events_file, issue_url)


def run_debugging_session(
    llm: LLM,
    working_dir: Path,
    query: str,
    repos: list[str],
    events_file: Path,
    issue_url: str,
):
    """Run the debugging session with the given configuration."""
    # Register and set up tools
    register_tool("TerminalTool", TerminalTool)
    register_tool("FileEditorTool", FileEditorTool)
    register_tool("TaskTrackerTool", TaskTrackerTool)

    tools = [
        Tool(name="TerminalTool"),
        Tool(name="FileEditorTool"),
        Tool(name="TaskTrackerTool"),
    ]

    # Create agent
    agent = Agent(llm=llm, tools=tools)

    # Collect LLM messages for debugging
    llm_messages = []

    def conversation_callback(event: Event):
        if isinstance(event, LLMConvertibleEvent):
            llm_messages.append(event.to_llm_message())

    # Start conversation with local workspace
    conversation = Conversation(
        agent=agent, workspace=str(working_dir), callbacks=[conversation_callback]
    )

    # Send the debugging task
    debugging_prompt = create_debugging_prompt(query, repos, events_file, issue_url)

    conversation.send_message(
        message=Message(
            role="user",
            content=[TextContent(text=debugging_prompt)],
        )
    )

    print("🤖 Starting debugging analysis...")
    try:
        conversation.run()

        print("\n" + "=" * 80)
        print("🎯 Debugging session completed!")
        print(f"📁 Results saved in: {working_dir}")
        print(f"💬 Total LLM messages: {len(llm_messages)}")

        # Show summary of what was accomplished
        print("\n📋 Session Summary:")
        print("- Queried PostHog events for error analysis")
        print("- Cloned and analyzed relevant repositories")
        print("- Investigated potential root causes")
        print("- Attempted error reproduction")

        # Check for cloned repositories
        if working_dir.exists():
            cloned_repos = [
                d for d in working_dir.iterdir() if d.is_dir() and (d / ".git").exists()
            ]
            if cloned_repos:
                print(
                    f"- Cloned repositories: {', '.join(d.name for d in cloned_repos)}"
                )
    finally:
        # Clean up conversation
        logger.info("Closing conversation...")
        conversation.close()


if __name__ == "__main__":
    main()
