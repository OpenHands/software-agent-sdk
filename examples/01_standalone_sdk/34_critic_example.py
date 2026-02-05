"""Iterative Refinement with Critic Model Example.

This is EXPERIMENTAL.

This example demonstrates how to use a critic model to shepherd an agent through
complex, multi-step tasks. The critic evaluates the agent's progress and provides
feedback that can trigger follow-up prompts when the agent hasn't completed the
task successfully.

Key concepts demonstrated:
1. Setting up a critic to evaluate agent actions in real-time
2. Capturing critic results via callbacks
3. Using low critic scores to trigger corrective follow-up prompts
4. Iterating until the task is completed successfully or max iterations reached

For All-Hands LLM proxy (llm-proxy.*.all-hands.dev), the critic is auto-configured
using the same base_url with /vllm suffix and "critic" as the model name.
"""

import os
import re
import tempfile
from pathlib import Path

from openhands.sdk import LLM, Agent, Conversation, Event, Tool
from openhands.sdk.critic import APIBasedCritic, CriticResult
from openhands.sdk.critic.base import CriticBase
from openhands.sdk.event import ActionEvent, MessageEvent
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.task_tracker import TaskTrackerTool
from openhands.tools.terminal import TerminalTool


# Configuration
# Higher threshold (70%) makes it more likely the agent needs multiple iterations
# to demonstrate the value of the critic model for iterative refinement
SUCCESS_THRESHOLD = float(os.getenv("CRITIC_SUCCESS_THRESHOLD", "0.70"))
MAX_ITERATIONS = int(os.getenv("MAX_ITERATIONS", "3"))


def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if value:
        return value
    raise ValueError(
        f"Missing required environment variable: {name}. "
        f"Set {name} before running this example."
    )


def get_default_critic(llm: LLM) -> CriticBase | None:
    """Auto-configure critic for All-Hands LLM proxy.

    When the LLM base_url matches `llm-proxy.*.all-hands.dev`, returns an
    APIBasedCritic configured with:
    - server_url: {base_url}/vllm
    - api_key: same as LLM
    - model_name: "critic"

    Returns None if base_url doesn't match or api_key is not set.
    """
    base_url = llm.base_url
    api_key = llm.api_key
    if base_url is None or api_key is None:
        return None

    # Match: llm-proxy.{env}.all-hands.dev (e.g., staging, prod, eval)
    pattern = r"^https?://llm-proxy\.[^./]+\.all-hands\.dev"
    if not re.match(pattern, base_url):
        return None

    return APIBasedCritic(
        server_url=f"{base_url.rstrip('/')}/vllm",
        api_key=api_key,
        model_name="critic",
    )


class CriticResultCollector:
    """Collects critic results from conversation events via callback."""

    def __init__(self) -> None:
        self.results: list[CriticResult] = []
        self.latest_result: CriticResult | None = None

    def callback(self, event: Event) -> None:
        """Callback to capture critic results from events."""
        if isinstance(event, (ActionEvent, MessageEvent)):
            if event.critic_result is not None:
                self.results.append(event.critic_result)
                self.latest_result = event.critic_result
                print(f"\n📊 Critic Score: {event.critic_result.score:.3f}")
                if event.critic_result.message:
                    print(f"   Details: {event.critic_result.message[:100]}...")

    def reset(self) -> None:
        """Reset collected results for a new iteration."""
        self.results = []
        self.latest_result = None


# Complex multi-step task prompt for iterative refinement demonstration.
# This task is designed to be challenging enough that the agent may not complete
# it perfectly on the first try, demonstrating the value of the critic in guiding
# improvements.
INITIAL_TASK_PROMPT = """\
Please help me create a complete full-stack task management application with user
authentication, task categories, and priority levels. This is a complex project
that requires careful attention to detail.

## Project Structure

Create a directory called 'taskmanager' with the following structure:

### 1. Backend (Flask API) - taskmanager/backend/

Create these files:
- `app.py` - Main Flask application with the following endpoints:
  - POST /api/auth/register - Register new user (username, email, password)
  - POST /api/auth/login - Login and return JWT token
  - GET /api/auth/me - Get current user profile (authenticated)
  - GET /api/tasks - Get all tasks for authenticated user (with filtering)
  - POST /api/tasks - Create a new task
  - PUT /api/tasks/<id> - Update a task
  - DELETE /api/tasks/<id> - Delete a task
  - GET /api/categories - Get all categories for user
  - POST /api/categories - Create a new category

- `models.py` - SQLAlchemy models:
  - User model (id, username, email, password_hash, created_at)
  - Task model (id, title, description, due_date, priority, completed, \
category_id, user_id, created_at, updated_at)
  - Category model (id, name, color, user_id)

- `auth.py` - Authentication utilities:
  - Password hashing with werkzeug.security
  - JWT token generation and validation
  - Login required decorator
  - Email validation

- `config.py` - Configuration (database URI, secret key, etc.)

- `requirements.txt` - Dependencies (flask, flask-sqlalchemy, pyjwt, werkzeug)

### 2. Frontend (HTML/CSS/JS) - taskmanager/frontend/

Create these files:
- `index.html` - Main page with:
  - Login/Register forms (toggle between them)
  - Task list display with filtering by category and priority
  - Add task form with category and priority selection
  - Category management section
  - Each task shows title, description, due date, priority badge, category, \
complete checkbox, delete button

- `styles.css` - Styling:
  - Clean, modern design with CSS variables for theming
  - Responsive layout (mobile-first)
  - Form styling with validation states
  - Task card styling with priority color indicators
  - Category badges with custom colors

- `app.js` - Frontend logic:
  - API calls to backend with proper error handling
  - JWT token storage in localStorage with expiration check
  - Dynamic DOM updates without page reload
  - Form validation (client-side)
  - Filter tasks by category and priority
  - Sort tasks by due date or priority

### 3. Tests - taskmanager/tests/

Create these files:
- `__init__.py` (empty)
- `test_auth.py` - Authentication tests (at least 5 tests):
  - Test user registration with valid data
  - Test duplicate username rejection
  - Test duplicate email rejection
  - Test login with valid credentials
  - Test login with invalid credentials

- `test_tasks.py` - Task API tests (at least 6 tests):
  - Test create task with all fields
  - Test create task with category
  - Test get tasks with filtering
  - Test update task priority
  - Test delete task
  - Test task isolation between users

- `test_categories.py` - Category API tests (at least 3 tests):
  - Test create category
  - Test get categories
  - Test category isolation between users

- `conftest.py` - Pytest fixtures for test database and test client

### 4. Documentation

- `README.md` with:
  - Project title and description
  - Features list (authentication, tasks, categories, priorities)
  - Installation instructions
  - How to run the backend
  - How to run tests
  - API documentation with example curl commands for ALL endpoints

- `taskmanager/__init__.py` (empty, makes it a package)

## Requirements

1. The backend must use SQLite database (stored as taskmanager.db)
2. Passwords must be hashed (never stored in plain text)
3. JWT tokens must expire after 24 hours
4. All task and category endpoints must require authentication
5. Tasks must have priority levels: low, medium, high
6. Categories must have customizable colors
7. Tests must pass when run with pytest

## Verification Steps

After creating all files:
1. List all created files to verify the structure
2. Install dependencies: pip install -r taskmanager/backend/requirements.txt
3. Run the tests: cd taskmanager && python -m pytest tests/ -v
4. Verify ALL tests pass (should be at least 14 tests total)

Please complete ALL of these steps. The task is only complete when:
- All files exist with proper implementation
- All 14+ tests pass
- The application structure is correct
- Categories and priorities are fully implemented
"""


# Follow-up prompt template for iterative refinement.
# Uses the critic's evaluation to craft specific guidance for improvement.
FOLLOWUP_PROMPT_TEMPLATE = """\
The task appears incomplete (iteration {iteration}, \
success likelihood: {score_percent:.1f}%).
{issues_text}

Please review what you've done so far and complete any remaining steps:

## Checklist - Backend (taskmanager/backend/)
- [ ] app.py - Flask app with ALL 9 endpoints:
  - POST /api/auth/register, POST /api/auth/login, GET /api/auth/me
  - GET/POST /api/tasks, PUT/DELETE /api/tasks/<id>
  - GET/POST /api/categories
- [ ] models.py - User, Task (with priority), and Category models
- [ ] auth.py - Password hashing, JWT, login_required, email validation
- [ ] config.py - Configuration settings
- [ ] requirements.txt - All dependencies listed

## Checklist - Frontend (taskmanager/frontend/)
- [ ] index.html - Login/Register, task list with filters, category management
- [ ] styles.css - Modern styling with priority colors and category badges
- [ ] app.js - API calls, JWT storage, filtering, sorting

## Checklist - Tests (taskmanager/tests/)
- [ ] __init__.py
- [ ] conftest.py - Pytest fixtures
- [ ] test_auth.py - At least 5 authentication tests (including email validation)
- [ ] test_tasks.py - At least 6 task API tests (including priority and category)
- [ ] test_categories.py - At least 3 category API tests

## Checklist - Documentation
- [ ] README.md - Full documentation with API examples for ALL endpoints
- [ ] taskmanager/__init__.py

## Critical Requirements Often Missed
- [ ] Category model with color field
- [ ] Task priority field (low, medium, high)
- [ ] test_categories.py file (separate from test_tasks.py)
- [ ] GET /api/auth/me endpoint
- [ ] At least 14 total tests

## Verification
1. List all files in taskmanager/ to see what exists
2. If any files are missing, create them
3. Run tests: cd taskmanager && python -m pytest tests/ -v
4. Verify at least 14 tests pass

List what files exist and what's missing, then complete the remaining tasks.
Use the finish tool only when ALL files exist and ALL 14+ tests pass.
"""


def get_followup_prompt(critic_result: CriticResult, iteration: int) -> str:
    """Generate a follow-up prompt based on critic feedback."""
    score_percent = critic_result.score * 100

    # Extract potential issues from critic metadata if available
    issues = []
    if critic_result.metadata and "categorized_features" in critic_result.metadata:
        categorized = critic_result.metadata["categorized_features"]
        if "agent_behavioral_issues" in categorized:
            issues = [
                f.get("display_name", f.get("name", "Unknown issue"))
                for f in categorized["agent_behavioral_issues"]
            ]

    issues_text = ""
    if issues:
        issues_text = f"\nPotential issues identified: {', '.join(issues)}"

    return FOLLOWUP_PROMPT_TEMPLATE.format(
        iteration=iteration,
        score_percent=score_percent,
        issues_text=issues_text,
    )


def main() -> None:
    """Run the iterative refinement example with critic model."""
    llm_api_key = get_required_env("LLM_API_KEY")
    llm = LLM(
        model=os.getenv("LLM_MODEL", "anthropic/claude-haiku-4-5"),
        api_key=llm_api_key,
        base_url=os.getenv("LLM_BASE_URL", None),
    )

    # Setup critic
    critic = get_default_critic(llm)
    if critic is None:
        print("⚠️  No All-Hands LLM proxy detected, trying explicit env vars...")
        critic = APIBasedCritic(
            server_url=get_required_env("CRITIC_SERVER_URL"),
            api_key=get_required_env("CRITIC_API_KEY"),
            model_name=get_required_env("CRITIC_MODEL_NAME"),
        )

    # Create agent with critic
    agent = Agent(
        llm=llm,
        tools=[
            Tool(name=TerminalTool.name),
            Tool(name=FileEditorTool.name),
            Tool(name=TaskTrackerTool.name),
        ],
        critic=critic,
    )

    # Create workspace and collector
    workspace = Path(tempfile.mkdtemp(prefix="critic_demo_"))
    print(f"📁 Created workspace: {workspace}")
    collector = CriticResultCollector()

    # Create conversation with callback
    conversation = Conversation(
        agent=agent,
        workspace=str(workspace),
        callbacks=[collector.callback],
    )

    print("\n" + "=" * 70)
    print("🚀 Starting Iterative Refinement with Critic Model")
    print("=" * 70)
    print(f"Success threshold: {SUCCESS_THRESHOLD:.0%}")
    print(f"Max iterations: {MAX_ITERATIONS}")

    # Initial task
    print("\n--- Iteration 1: Initial Task ---")
    conversation.send_message(INITIAL_TASK_PROMPT)
    conversation.run()

    iteration = 1
    while iteration < MAX_ITERATIONS:
        # Check critic result
        if collector.latest_result is None:
            print("\n⚠️  No critic result available, assuming task incomplete")
            score = 0.0
        else:
            score = collector.latest_result.score

        print(f"\n📈 Iteration {iteration} final score: {score:.3f}")

        if score >= SUCCESS_THRESHOLD:
            print(f"✅ Success threshold ({SUCCESS_THRESHOLD:.0%}) met!")
            break

        # Prepare for next iteration - save latest_result BEFORE reset
        iteration += 1
        last_result = collector.latest_result or CriticResult(score=0.0, message=None)
        collector.reset()

        print(f"\n--- Iteration {iteration}: Follow-up Refinement ---")
        print(f"Score {score:.3f} < threshold {SUCCESS_THRESHOLD:.3f}, sending...")

        followup_prompt = get_followup_prompt(last_result, iteration)
        conversation.send_message(followup_prompt)
        conversation.run()

    # Final summary
    print("\n" + "=" * 70)
    print("📊 ITERATIVE REFINEMENT COMPLETE")
    print("=" * 70)
    print(f"Total iterations: {iteration}")

    if collector.latest_result:
        final_score = collector.latest_result.score
        print(f"Final critic score: {final_score:.3f}")
        print(f"Success: {'✅ YES' if final_score >= SUCCESS_THRESHOLD else '❌ NO'}")
    else:
        print("Final critic score: N/A (no critic results)")

    # List created files
    print("\nCreated files:")
    for path in sorted(workspace.rglob("*")):
        if path.is_file():
            relative = path.relative_to(workspace)
            print(f"  - {relative}")

    # Report cost
    cost = llm.metrics.accumulated_cost
    print(f"\nEXAMPLE_COST: {cost:.4f}")


if __name__ == "__main__":
    main()
