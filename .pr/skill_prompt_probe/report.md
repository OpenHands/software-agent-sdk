Example basis: /workspace/project/software-agent-sdk/examples/05_skills_and_plugins/01_loading_agentskills/main.py
Extensions repo: /workspace/project/extensions
Completion log: /workspace/project/software-agent-sdk/.pr/skill_prompt_probe/completions/openai__gpt-5-nano-1775007512.342-9945.json

| skill | full description length | prompt description length | sent in full |
| --- | ---: | ---: | --- |
| babysit-pr | 552 | 552 | yes |
| readiness-report | 432 | 432 | yes |
| frontend-design | 399 | 399 | yes |

Prompt descriptions:

## babysit-pr
Source: `/workspace/project/extensions/skills/babysit-pr/SKILL.md`

Babysit a GitHub pull request by continuously polling CI checks/workflow runs, new review comments, and mergeability state until the PR is ready to merge (or merged/closed). Diagnose failures, retry likely flaky failures up to 3 times, fix/push branch-related issues when appropriate, and stop only when user help is required (e.g., CI infrastructure outages, exhausted flaky retries, permissions, or ambiguous/blocking situations). Use when the user asks to monitor/watch/babysit a PR, watch CI, handle review comments, or keep an eye on mergeability.

## readiness-report
Source: `/workspace/project/extensions/skills/readiness-report/SKILL.md`

Evaluate how well a codebase supports autonomous AI development. Analyzes repositories across eight technical pillars (Style & Validation, Build System, Testing, Documentation, Dev Environment, Debugging & Observability, Security, Task Discovery) and five maturity levels. Use when users request `/readiness-report` or want to assess agent readiness, codebase maturity, or identify gaps preventing effective AI-assisted development.

## frontend-design
Source: `/workspace/project/extensions/skills/frontend-design/SKILL.md`

Create distinctive, production-grade frontend interfaces with high design quality. Use this skill when the user asks to build web components, pages, artifacts, posters, or applications (examples include websites, landing pages, dashboards, React components, HTML/CSS layouts, or when styling/beautifying any web UI). Generates creative, polished code and UI design that avoids generic AI aesthetics.
