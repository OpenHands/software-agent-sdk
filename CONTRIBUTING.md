# Contributing

Thanks for your interest in contributing to the OpenHands TypeScript client.
This repository provides the browser-compatible TypeScript SDK for the
OpenHands Agent Server.

## Development setup

### Prerequisites

- Node.js 20+
- npm 10+

### Install dependencies

```bash
npm ci
```

## Common commands

```bash
npm run build
npm run lint
npm test
```

Integration tests require a running agent-server container and LLM credentials:

```bash
export LLM_API_KEY="your-api-key"
export LLM_MODEL="anthropic/claude-sonnet-4-5-20250929"

docker run -d --name agent-server -p 8010:8000 \
  -v /tmp/agent-workspace:/workspace \
  ghcr.io/openhands/agent-server:71b070d-python

npm run test:integration
```

## Project guidelines

- Keep code in `src/` browser-compatible; avoid Node.js built-in modules there.
- Prefer focused, minimal changes over broad refactors.
- Add or update tests when changing runtime behavior.
- Keep the root SDK surface ergonomic; lower-level endpoint clients belong in
  `@openhands/typescript-client/clients`.

## ACP provider registry

The data in `src/models/acp-providers.json` mirrors
`openhands.sdk.settings.acp_providers.ACP_PROVIDERS` in
[software-agent-sdk](https://github.com/OpenHands/software-agent-sdk). The
Python module is the canonical source — edit `acp-providers.json` here when
it changes upstream. The `validate-acp-providers` CI job diffs the two on
every PR; to run it locally:

```bash
pip install -r scripts/requirements-acp-check.txt
python scripts/check-acp-drift.py
```

## Pull requests

- Open focused PRs with a clear description of what changed and why.
- Make sure the relevant checks pass before requesting review.
- Use conventional prefixes in the PR title when possible, such as `feat`,
  `fix`, `docs`, `refactor`, `test`, `build`, `ci`, or `chore`.
- Include screenshots or recordings when changing user-facing example apps or
  documentation visuals.

## Need help?

- Open a GitHub issue in this repository.
- Reach out in the OpenHands community Slack: https://openhands.dev/joinslack
- Review the main project docs: https://docs.all-hands.dev/
