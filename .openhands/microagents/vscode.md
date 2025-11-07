---
name: vscode
version: 1.1.0
agent: CodeActAgent
triggers:
  - vscode
---

# VSCode Quick Start for Agent SDK Repo

## Open the project in a fresh VSCode window
```bash
code -n <path-to-your-agent-sdk-clone>
```

If `code` is not on PATH, launch VSCode manually, then **File → Open...** and select the repository root.

## Use the repo virtual environment
The workspace sets the interpreter automatically via `.vscode/settings.json`:
```json
{
  "python.defaultInterpreterPath": "${workspaceFolder}/.venv/bin/python",
  "python.terminal.activateEnvironment": true,
  "python.envFile": "${workspaceFolder}/.env"
}
```
Verify inside VSCode with **Python: Select Interpreter → agent-sdk-clone/.venv**.

## Run / debug example 25 (LLM profiles)
Launch configuration lives in `.vscode/launch.json`:
```json
{
  "name": "Example 25 – Debug LLM Profiles",
  "type": "python",
  "request": "launch",
  "python": "${workspaceFolder}/.venv/bin/python",
  "program": "${workspaceFolder}/examples/01_standalone_sdk/25_llm_profiles.py",
  "console": "integratedTerminal",
  "justMyCode": false,
  "envFile": "${workspaceFolder}/.env"
}
```
Steps:
1. Ensure `.env` contains your `LLM_API_KEY` (and optional `LLM_PROFILE_NAME`).
2. In VSCode, open the **Run and Debug** view.
3. Choose **Example 25 – Debug LLM Profiles** and press **Start Debugging** (F5).

This will start the script under debugpy with the repo’s virtualenv, attach breakpoints as needed, and reuse environment variables from `.env`.
