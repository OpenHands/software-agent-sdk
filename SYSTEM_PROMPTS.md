# System Prompt Sections Inventory

This document inventories every system prompt section (including optional/conditional
sections) used by the OpenHands SDK. It also lists additional sections defined in the
OpenHands and OpenHands-CLI repositories.

## software-agent-sdk

### Base system prompt (`system_prompt.j2`)

Source: https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/system_prompt.j2

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
  - Source: https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/self_documentation.j2
- `<SECURITY>`: Security policy include (allowed, consent, forbidden actions).
  [Lines 77-79](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/system_prompt.j2#L77-L79)
  - Source: https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/security_policy.j2
- `<SECURITY_RISK_ASSESSMENT>`: Conditional include when `llm_security_analyzer` is
  enabled. [Lines 81-85](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/system_prompt.j2#L81-L85)
  - Source: https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/security_risk_assessment.j2
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
    - https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/model_specific/anthropic_claude.j2
    - https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/model_specific/google_gemini.j2
    - https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/model_specific/openai_gpt/gpt-5.j2
    - https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/model_specific/openai_gpt/gpt-5-codex.j2

### Alternate system prompts

Source directory: https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/prompts/

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

Source: https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/context/prompts/templates/system_message_suffix.j2

- `<REPO_CONTEXT>`: Repo-specific instructions from legacy skills.
  [Lines 2-11](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/context/prompts/templates/system_message_suffix.j2#L2-L11)
- `<SKILLS>`: List of available skills and how to use them.
  [Lines 14-20](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/context/prompts/templates/system_message_suffix.j2#L14-L20)
- `<CUSTOM_SECRETS>`: Secret injection and usage guidance, plus available secrets.
  [Lines 27-41](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/context/prompts/templates/system_message_suffix.j2#L27-L41)

### Skill-triggered info blocks (conditional)

`AgentContext.get_user_message_suffix()` can inject skill knowledge blocks into the
conversation when triggers match.

Source: https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/context/prompts/templates/skill_knowledge_info.j2

- `<EXTRA_INFO>`: Injected skill content with optional location hints.
  [Lines 2-11](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/context/prompts/templates/skill_knowledge_info.j2#L2-L11)

### Ask-agent template (conditional)

Used for question-only interactions where tools must not be called.

Source: https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/context/prompts/templates/ask_agent_template.j2

- `<QUESTION>`: Wraps a question-only request.
  [Lines 1-11](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/context/prompts/templates/ask_agent_template.j2#L1-L11)
- `<IMPORTANT>`: Explicit instruction to avoid tool calls and answer only.
  [Lines 8-10](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/context/prompts/templates/ask_agent_template.j2#L8-L10)

## OpenHands repository

### CodeAct agent system prompt

Source: https://github.com/OpenHands/OpenHands/blob/bef9b80b9dc6061d27a748ef2115120a6a2feb87/openhands/agenthub/codeact_agent/prompts/system_prompt.j2

Additional section compared to SDK base:

- `<DOCUMENTATION>`: Documentation handling rules (where to place docs and
  whether to include them in VCS).
  [Lines 97-105](https://github.com/OpenHands/OpenHands/blob/bef9b80b9dc6061d27a748ef2115120a6a2feb87/openhands/agenthub/codeact_agent/prompts/system_prompt.j2#L97-L105)

### Long-horizon additions

Source: https://github.com/OpenHands/OpenHands/blob/bef9b80b9dc6061d27a748ef2115120a6a2feb87/openhands/agenthub/codeact_agent/prompts/system_prompt_long_horizon.j2

- `<TASK_TRACKING_PERSISTENCE>`: Continue task tracking across condensation events.
  [Lines 42-46](https://github.com/OpenHands/OpenHands/blob/bef9b80b9dc6061d27a748ef2115120a6a2feb87/openhands/agenthub/codeact_agent/prompts/system_prompt_long_horizon.j2#L42-L46)

### ReadOnly agent prompt

Source: https://github.com/OpenHands/OpenHands/blob/bef9b80b9dc6061d27a748ef2115120a6a2feb87/openhands/agenthub/readonly_agent/prompts/system_prompt.j2

- `<CAPABILITIES>`: Read-only tool list and restrictions.
  [Lines 7-19](https://github.com/OpenHands/OpenHands/blob/bef9b80b9dc6061d27a748ef2115120a6a2feb87/openhands/agenthub/readonly_agent/prompts/system_prompt.j2#L7-L19)
- `<GUIDELINES>`: Read-only behavior guidelines and escalation instructions.
  [Lines 21-34](https://github.com/OpenHands/OpenHands/blob/bef9b80b9dc6061d27a748ef2115120a6a2feb87/openhands/agenthub/readonly_agent/prompts/system_prompt.j2#L21-L34)

### Other OpenHands prompts

Source directory: https://github.com/OpenHands/OpenHands/tree/bef9b80b9dc6061d27a748ef2115120a6a2feb87/openhands/agenthub/codeact_agent/prompts/

- `system_prompt_interactive.j2`: Same `<INTERACTION_RULES>` section as SDK.
- `system_prompt_tech_philosophy.j2`: Same `<TECHNICAL_PHILOSOPHY>` section as SDK.

## OpenHands-CLI repository

No system prompt templates were found in `OpenHands-CLI` (searching for
`system_prompt.j2` and related prompt template names in the repository).

Repo: https://github.com/OpenHands/OpenHands-CLI

## Related issues

- Behavior initiative issue: https://github.com/OpenHands/software-agent-sdk/issues/1320
- This inventory issue: https://github.com/OpenHands/software-agent-sdk/issues/1965
