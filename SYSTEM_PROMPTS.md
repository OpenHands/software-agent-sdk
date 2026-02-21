# System Prompt Sections Inventory

This document inventories every system prompt section (including optional/conditional
sections) used by the OpenHands SDK. It also lists additional sections defined in the
OpenHands and OpenHands-CLI repositories.

## software-agent-sdk

### Base system prompt (`system_prompt.j2`)

Source: [system_prompt.j2](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/system_prompt.j2)

- `<ROLE>`: Primary responsibilities and guidance on answering questions without
  implementing fixes. [Lines 3-6](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/system_prompt.j2#L3-L6)
- `<MEMORY>`: Repository memory via `AGENTS.md` and skills documentation link.
  [Lines 8-13](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/system_prompt.j2#L8-L13)
- `<EFFICIENCY>`: Guidance on batching actions and efficient exploration.
  [Lines 15-18](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/system_prompt.j2#L15-L18)
- `<FILE_SYSTEM_GUIDELINES>`: File path handling, edit-in-place, and duplication
  avoidance rules. [Lines 20-30](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/system_prompt.j2#L20-L30)
- `<CODE_QUALITY>`: Clean code expectations, minimal comments, and import guidance.
  [Lines 32-38](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/system_prompt.j2#L32-L38)
- `<VERSION_CONTROL>`: Git configuration and safety guidance for commits/ops.
  [Lines 40-47](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/system_prompt.j2#L40-L47)
- `<PULL_REQUESTS>`: PR creation/update rules (only when asked, preserve context).
  [Lines 49-54](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/system_prompt.j2#L49-L54)
- `<PROBLEM_SOLVING_WORKFLOW>`: Explore → analyze → test → implement → verify.
  [Lines 56-71](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/system_prompt.j2#L56-L71)
- `<SELF_DOCUMENTATION>`: Includes documentation lookup rules for OpenHands products.
  [Lines 73-75](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/system_prompt.j2#L73-L75)
  - Source: [self_documentation.j2](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/self_documentation.j2)
- `<SECURITY>`: Security policy include (allowed, consent, forbidden actions).
  [Lines 77-79](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/system_prompt.j2#L77-L79)
  - Source: [security_policy.j2](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/security_policy.j2)
- `<SECURITY_RISK_ASSESSMENT>`: Conditional include when `llm_security_analyzer` is
  enabled. [Lines 81-85](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/system_prompt.j2#L81-L85)
  - Source: [security_risk_assessment.j2](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/security_risk_assessment.j2)
- `<EXTERNAL_SERVICES>`: Prefer APIs for external services; use browser only if needed.
  [Lines 87-90](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/system_prompt.j2#L87-L90)
- `<ENVIRONMENT_SETUP>`: Dependency installation guidance.
  [Lines 92-99](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/system_prompt.j2#L92-L99)
- `<TROUBLESHOOTING>`: Diagnostic workflow for repeated failures.
  [Lines 101-108](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/system_prompt.j2#L101-L108)
- `<PROCESS_MANAGEMENT>`: Safe process termination rules.
  [Lines 110-116](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/system_prompt.j2#L110-L116)
- `<IMPORTANT>`: Conditional include for model-specific instructions when
  `model_family`/`model_variant` are provided.
  [Lines 118-133](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/system_prompt.j2#L118-L133)
  - Sources:
    - [anthropic_claude.j2](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/model_specific/anthropic_claude.j2)
    - [google_gemini.j2](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/model_specific/google_gemini.j2)
    - [gpt-5.j2](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/model_specific/openai_gpt/gpt-5.j2)
    - [gpt-5-codex.j2](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/model_specific/openai_gpt/gpt-5-codex.j2)

### Alternate system prompts

Source directory: [openhands/sdk/agent/prompts/](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/)

- `system_prompt_interactive.j2`: Adds `<INTERACTION_RULES>` for ambiguity handling,
  clarification, file existence checks, multilingual support, and avoiding wasted work.
  [Lines 3-14](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/system_prompt_interactive.j2#L3-L14)
- `system_prompt_long_horizon.j2`: Adds `<TASK_MANAGEMENT>` guidance for using the
  task tracker and example workflows.
  [Lines 3-40](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/system_prompt_long_horizon.j2#L3-L40)
- `system_prompt_planning.j2`: Separate planning agent with its own sections.
  - `<ROLE>`: Planning-only role. [Lines 3-6](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/system_prompt_planning.j2#L3-L6)
  - `<IMPORTANT_PRINCIPLES>`: Planning principles and clarifications.
    [Lines 8-15](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/system_prompt_planning.j2#L8-L15)
  - `<EFFICIENCY>`: Efficient exploration guidance.
    [Lines 17-20](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/system_prompt_planning.j2#L17-L20)
  - `<FILE_SYSTEM_GUIDELINES>`: Path handling rules.
    [Lines 22-24](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/system_prompt_planning.j2#L22-L24)
  - `<PLANNING_WORKFLOW>`: Multi-phase planning workflow.
    [Lines 26-83](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/system_prompt_planning.j2#L26-L83)
  - `<PLAN_SCOPE>`: Scope rules for planning output.
    [Lines 85-90](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/system_prompt_planning.j2#L85-L90)
  - `<PLAN_STRUCTURE>`: Injected plan structure template.
    [Lines 92-94](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/system_prompt_planning.j2#L92-L94)
- `system_prompt_tech_philosophy.j2`: Adds `<TECHNICAL_PHILOSOPHY>` (Linus-inspired
  engineering guidance). [Lines 3-122](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/system_prompt_tech_philosophy.j2#L3-L122)

### System message suffix (conditional)

`AgentContext.get_system_message_suffix()` appends additional sections to the
system prompt when repo skills, custom suffix, or secrets are present.

Source: [system_message_suffix.j2](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/context/prompts/templates/system_message_suffix.j2)

- `<REPO_CONTEXT>`: Repo-specific instructions from legacy skills.
  [Lines 2-11](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/context/prompts/templates/system_message_suffix.j2#L2-L11)
- `<SKILLS>`: List of available skills and how to use them.
  [Lines 14-20](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/context/prompts/templates/system_message_suffix.j2#L14-L20)
- `<CUSTOM_SECRETS>`: Secret injection and usage guidance, plus available secrets.
  [Lines 27-41](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/context/prompts/templates/system_message_suffix.j2#L27-L41)

### Skill-triggered info blocks (conditional)

`AgentContext.get_user_message_suffix()` can inject skill knowledge blocks into the
conversation when triggers match.

Source: [skill_knowledge_info.j2](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/context/prompts/templates/skill_knowledge_info.j2)

- `<EXTRA_INFO>`: Injected skill content with optional location hints.
  [Lines 2-11](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/context/prompts/templates/skill_knowledge_info.j2#L2-L11)

### Ask-agent template (conditional)

Used for question-only interactions where tools must not be called.

Source: [ask_agent_template.j2](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/context/prompts/templates/ask_agent_template.j2)

- `<QUESTION>`: Wraps a question-only request.
  [Lines 1-11](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/context/prompts/templates/ask_agent_template.j2#L1-L11)
- `<IMPORTANT>`: Explicit instruction to avoid tool calls and answer only.
  [Lines 8-10](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/context/prompts/templates/ask_agent_template.j2#L8-L10)

### All Jinja prompt templates in this repository

Complete list of `*.j2` / `*.jinja*` templates in this repo (excluding virtualenvs),
including system prompts, prompt includes, and other prompt pieces.

Agent prompts:
- [system_prompt.j2](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/system_prompt.j2)
- [system_prompt_interactive.j2](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/system_prompt_interactive.j2)
- [system_prompt_long_horizon.j2](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/system_prompt_long_horizon.j2)
- [system_prompt_planning.j2](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/system_prompt_planning.j2)
- [system_prompt_tech_philosophy.j2](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/system_prompt_tech_philosophy.j2)
- [self_documentation.j2](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/self_documentation.j2)
- [security_policy.j2](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/security_policy.j2)
- [security_risk_assessment.j2](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/security_risk_assessment.j2)
- [in_context_learning_example.j2](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/in_context_learning_example.j2)
- [in_context_learning_example_suffix.j2](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/in_context_learning_example_suffix.j2)
- [anthropic_claude.j2](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/model_specific/anthropic_claude.j2)
- [google_gemini.j2](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/model_specific/google_gemini.j2)
- [gpt-5.j2](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/model_specific/openai_gpt/gpt-5.j2)
- [gpt-5-codex.j2](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/model_specific/openai_gpt/gpt-5-codex.j2)

Context/condenser prompts:
- [summarizing_prompt.j2](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/context/condenser/prompts/summarizing_prompt.j2)
- [ask_agent_template.j2](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/context/prompts/templates/ask_agent_template.j2)
- [skill_knowledge_info.j2](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/context/prompts/templates/skill_knowledge_info.j2)
- [system_message_suffix.j2](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/context/prompts/templates/system_message_suffix.j2)

Tool prompt templates:
- [delegate_tool_description.j2](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-tools/openhands/tools/delegate/templates/delegate_tool_description.j2)

Example-only prompts (used by example scripts, not the SDK runtime):
- [debug_prompt.jinja](https://github.com/OpenHands/software-agent-sdk/blob/main/examples/03_github_workflows/04_datadog_debugging/debug_prompt.jinja) (Datadog debugging example)
- [debug_prompt.jinja](https://github.com/OpenHands/software-agent-sdk/blob/main/examples/03_github_workflows/05_posthog_debugging/debug_prompt.jinja) (PostHog debugging example)


## OpenHands repository (V1 prompt/template sources)

Repo: [OpenHands/OpenHands](https://github.com/OpenHands/OpenHands)

Note: The OpenHands repo still contains legacy (V0) agent prompt templates under
`openhands/agenthub/**/prompts/` and related code explicitly tagged `Tag: Legacy-V0`.
Those legacy templates are intentionally excluded here.

### Integrations resolver templates (`openhands/integrations/templates/resolver/**.j2`)

These templates are used to generate per-provider resolver conversation instructions
and prompts (e.g., the “DO NOT leave any comments …” style constraints used in PR
update flows).

- [summary_prompt.j2](https://github.com/OpenHands/OpenHands/blob/main/openhands/integrations/templates/resolver/summary_prompt.j2)

GitHub:
- [issue_conversation_instructions.j2](https://github.com/OpenHands/OpenHands/blob/main/openhands/integrations/templates/resolver/github/issue_conversation_instructions.j2)
- [issue_prompt.j2](https://github.com/OpenHands/OpenHands/blob/main/openhands/integrations/templates/resolver/github/issue_prompt.j2)
- [pr_update_conversation_instructions.j2](https://github.com/OpenHands/OpenHands/blob/main/openhands/integrations/templates/resolver/github/pr_update_conversation_instructions.j2)
- [pr_update_prompt.j2](https://github.com/OpenHands/OpenHands/blob/main/openhands/integrations/templates/resolver/github/pr_update_prompt.j2)

GitLab:
- [issue_conversation_instructions.j2](https://github.com/OpenHands/OpenHands/blob/main/openhands/integrations/templates/resolver/gitlab/issue_conversation_instructions.j2)
- [issue_prompt.j2](https://github.com/OpenHands/OpenHands/blob/main/openhands/integrations/templates/resolver/gitlab/issue_prompt.j2)
- [mr_update_conversation_instructions.j2](https://github.com/OpenHands/OpenHands/blob/main/openhands/integrations/templates/resolver/gitlab/mr_update_conversation_instructions.j2)
- [mr_update_prompt.j2](https://github.com/OpenHands/OpenHands/blob/main/openhands/integrations/templates/resolver/gitlab/mr_update_prompt.j2)

Bitbucket:
- [issue_conversation_instructions.j2](https://github.com/OpenHands/OpenHands/blob/main/openhands/integrations/templates/resolver/bitbucket/issue_conversation_instructions.j2)
- [issue_prompt.j2](https://github.com/OpenHands/OpenHands/blob/main/openhands/integrations/templates/resolver/bitbucket/issue_prompt.j2)
- [pr_update_conversation_instructions.j2](https://github.com/OpenHands/OpenHands/blob/main/openhands/integrations/templates/resolver/bitbucket/pr_update_conversation_instructions.j2)
- [pr_update_prompt.j2](https://github.com/OpenHands/OpenHands/blob/main/openhands/integrations/templates/resolver/bitbucket/pr_update_prompt.j2)

Azure DevOps:
- [issue_conversation_instructions.j2](https://github.com/OpenHands/OpenHands/blob/main/openhands/integrations/templates/resolver/azure_devops/issue_conversation_instructions.j2)
- [issue_prompt.j2](https://github.com/OpenHands/OpenHands/blob/main/openhands/integrations/templates/resolver/azure_devops/issue_prompt.j2)
- [pr_update_conversation_instructions.j2](https://github.com/OpenHands/OpenHands/blob/main/openhands/integrations/templates/resolver/azure_devops/pr_update_conversation_instructions.j2)
- [pr_update_prompt.j2](https://github.com/OpenHands/OpenHands/blob/main/openhands/integrations/templates/resolver/azure_devops/pr_update_prompt.j2)

Jira:
- [jira_existing_conversation.j2](https://github.com/OpenHands/OpenHands/blob/main/openhands/integrations/templates/resolver/jira/jira_existing_conversation.j2)
- [jira_instructions.j2](https://github.com/OpenHands/OpenHands/blob/main/openhands/integrations/templates/resolver/jira/jira_instructions.j2)
- [jira_new_conversation.j2](https://github.com/OpenHands/OpenHands/blob/main/openhands/integrations/templates/resolver/jira/jira_new_conversation.j2)

Jira DC:
- [jira_dc_existing_conversation.j2](https://github.com/OpenHands/OpenHands/blob/main/openhands/integrations/templates/resolver/jira_dc/jira_dc_existing_conversation.j2)
- [jira_dc_instructions.j2](https://github.com/OpenHands/OpenHands/blob/main/openhands/integrations/templates/resolver/jira_dc/jira_dc_instructions.j2)
- [jira_dc_new_conversation.j2](https://github.com/OpenHands/OpenHands/blob/main/openhands/integrations/templates/resolver/jira_dc/jira_dc_new_conversation.j2)

Linear:
- [linear_existing_conversation.j2](https://github.com/OpenHands/OpenHands/blob/main/openhands/integrations/templates/resolver/linear/linear_existing_conversation.j2)
- [linear_instructions.j2](https://github.com/OpenHands/OpenHands/blob/main/openhands/integrations/templates/resolver/linear/linear_instructions.j2)
- [linear_new_conversation.j2](https://github.com/OpenHands/OpenHands/blob/main/openhands/integrations/templates/resolver/linear/linear_new_conversation.j2)

Slack:
- [user_message_conversation_instructions.j2](https://github.com/OpenHands/OpenHands/blob/main/openhands/integrations/templates/resolver/slack/user_message_conversation_instructions.j2)

### Suggested-task prompt templates (`openhands/integrations/templates/suggested_task/*.j2`)

Used by `openhands/integrations/service_types.py` in OpenHands
([FileSystemLoader + template selection, lines 92-104](https://github.com/OpenHands/OpenHands/blob/main/openhands/integrations/service_types.py#L92-L104)):

- [merge_conflict_prompt.j2](https://github.com/OpenHands/OpenHands/blob/main/openhands/integrations/templates/suggested_task/merge_conflict_prompt.j2)
- [failing_checks_prompt.j2](https://github.com/OpenHands/OpenHands/blob/main/openhands/integrations/templates/suggested_task/failing_checks_prompt.j2)
- [unresolved_comments_prompt.j2](https://github.com/OpenHands/OpenHands/blob/main/openhands/integrations/templates/suggested_task/unresolved_comments_prompt.j2)
- [open_issue_prompt.j2](https://github.com/OpenHands/OpenHands/blob/main/openhands/integrations/templates/suggested_task/open_issue_prompt.j2)

### Resolver prompt pieces (`openhands/resolver/prompts/**.jinja`)

Loaded by resolver code, for example:
- default resolve prompt selection in
  `openhands/resolver/issue_resolver.py`
  ([lines 114-124](https://github.com/OpenHands/OpenHands/blob/main/openhands/resolver/issue_resolver.py#L114-L124))
- guess-success checks referenced in
  `openhands/resolver/interfaces/issue_definitions.py`
  ([lines 197-254](https://github.com/OpenHands/OpenHands/blob/main/openhands/resolver/interfaces/issue_definitions.py#L197-L254),
  [line 391](https://github.com/OpenHands/OpenHands/blob/main/openhands/resolver/interfaces/issue_definitions.py#L391))
- PR change summary prompt referenced in
  `openhands/resolver/send_pull_request.py`
  ([line 542](https://github.com/OpenHands/OpenHands/blob/main/openhands/resolver/send_pull_request.py#L542))

Guess-success prompts:
- [issue-success-check.jinja](https://github.com/OpenHands/OpenHands/blob/main/openhands/resolver/prompts/guess_success/issue-success-check.jinja)
- [pr-feedback-check.jinja](https://github.com/OpenHands/OpenHands/blob/main/openhands/resolver/prompts/guess_success/pr-feedback-check.jinja)
- [pr-review-check.jinja](https://github.com/OpenHands/OpenHands/blob/main/openhands/resolver/prompts/guess_success/pr-review-check.jinja)
- [pr-thread-check.jinja](https://github.com/OpenHands/OpenHands/blob/main/openhands/resolver/prompts/guess_success/pr-thread-check.jinja)

Resolve prompts:
- [basic.jinja](https://github.com/OpenHands/OpenHands/blob/main/openhands/resolver/prompts/resolve/basic.jinja)
- [basic-conversation-instructions.jinja](https://github.com/OpenHands/OpenHands/blob/main/openhands/resolver/prompts/resolve/basic-conversation-instructions.jinja)
- [basic-followup.jinja](https://github.com/OpenHands/OpenHands/blob/main/openhands/resolver/prompts/resolve/basic-followup.jinja)
- [basic-followup-conversation-instructions.jinja](https://github.com/OpenHands/OpenHands/blob/main/openhands/resolver/prompts/resolve/basic-followup-conversation-instructions.jinja)
- [basic-with-tests.jinja](https://github.com/OpenHands/OpenHands/blob/main/openhands/resolver/prompts/resolve/basic-with-tests.jinja)
- [basic-with-tests-conversation-instructions.jinja](https://github.com/OpenHands/OpenHands/blob/main/openhands/resolver/prompts/resolve/basic-with-tests-conversation-instructions.jinja)
- [pr-changes-summary.jinja](https://github.com/OpenHands/OpenHands/blob/main/openhands/resolver/prompts/resolve/pr-changes-summary.jinja)

### Microagent remember-prompt template

- [generate_remember_prompt.j2](https://github.com/OpenHands/OpenHands/blob/main/openhands/microagent/prompts/generate_remember_prompt.j2)

Used by OpenHands server code
([manage_conversations.py lines 715-718](https://github.com/OpenHands/OpenHands/blob/main/openhands/server/routes/manage_conversations.py#L715-L718)).

### Enterprise solvability prompt sources

- [summary_system_message.j2](https://github.com/OpenHands/OpenHands/blob/main/enterprise/integrations/solvability/prompts/summary_system_message.j2)
- [summary_user_message.j2](https://github.com/OpenHands/OpenHands/blob/main/enterprise/integrations/solvability/prompts/summary_user_message.j2)

This repo also embeds a prompt string in JSON at:
- [default-classifier.json](https://github.com/OpenHands/OpenHands/blob/main/enterprise/integrations/solvability/data/default-classifier.json)
  (`featurizer.system_prompt`)

## OpenHands-CLI repository

Repo: [OpenHands-CLI](https://github.com/OpenHands/OpenHands-CLI)

- No `*.j2` / `*.jinja*` templates exist in this repo.
- The CLI influences the SDK system prompt and system-message content via Python:
  - passes `system_prompt_kwargs={"cli_mode": True}` when constructing the SDK `Agent`
    ([openhands_cli/utils.py line 178](https://github.com/OpenHands/OpenHands-CLI/blob/main/openhands_cli/utils.py#L178))
  - sets `AgentContext(system_message_suffix=...)` (working directory + OS description)
    ([openhands_cli/stores/agent_store.py lines 383-391](https://github.com/OpenHands/OpenHands-CLI/blob/main/openhands_cli/stores/agent_store.py#L383-L391))

## Related issues

- Behavior initiative issue: [#1320](https://github.com/OpenHands/software-agent-sdk/issues/1320)
- This inventory issue: [#1965](https://github.com/OpenHands/software-agent-sdk/issues/1965)
