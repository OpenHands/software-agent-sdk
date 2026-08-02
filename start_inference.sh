#!/usr/bin/env bash
# ============================================================
# Pyromind Agent Server - Startup Script
# ============================================================
# Usage:
#   chmod +x start_inference.sh
#   ./start_inference.sh
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export SOFTWARE_AGENT_SDK_DIR="${SOFTWARE_AGENT_SDK_DIR:-${SCRIPT_DIR}}"

# ----------------------------------------------------------
# LLM Configuration
# ----------------------------------------------------------
# LiteLLM requires a provider prefix (e.g. openai/) for custom OpenAI-compatible endpoints.
export LLM_BASE_URL="${LLM_BASE_URL:-http://208.64.254.187:8000/v1}"
if [[ -z "${OPENAI_API_KEY:-}" && -n "${LLM_API_KEY:-}" ]]; then
  export OPENAI_API_KEY="${LLM_API_KEY}"
fi
if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "ERROR: OPENAI_API_KEY is required. Export it before running start.sh." >&2
  exit 1
fi
export LLM_MODEL="${LLM_MODEL:-openai/glm-5.2-fp8}"
if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "ERROR: OPENAI_API_KEY is required. Set it in start_inference.sh." >&2
  exit 1
fi
export OPENAI_API_KEY

if [[ -n "${OPENROUTER_API_KEY:-}" ]]; then
  export DF_API_BASE_URL="https://openrouter.ai/api/v1"
  export DF_API_URL="https://openrouter.ai/api/v1/chat/completions"
  export DF_MODEL_NAME="google/gemma-4-31b-it"
  export DF_API_KEY="${OPENROUTER_API_KEY}"
else
  unset DF_API_BASE_URL DF_API_URL DF_MODEL_NAME DF_API_KEY
  echo "WARN: OPENROUTER_API_KEY is not set; vision DataFlow will use the conversation model fallback." >&2
fi

# ----------------------------------------------------------
# Agent Server Configuration
# ----------------------------------------------------------
# Session API key for authenticating requests (leave empty for unsecured dev mode)
export SESSION_API_KEY="${SESSION_API_KEY:-pyromind_agent}"

# Secret key for encrypting LLM API keys in stored conversations
# Generate with: openssl rand -hex 32
if [[ -n "${OH_SECRET_KEY:-}" ]]; then
  export OH_SECRET_KEY
elif [[ -n "${SESSION_API_KEY}" ]]; then
  export OH_SECRET_KEY="${SESSION_API_KEY}"
fi

export OH_ENABLE_PYROMIND_JWT_AUTH="${OH_ENABLE_PYROMIND_JWT_AUTH:-true}"

# Keep the server in local development mode while consuming pre workflow events.
export KAFKA_ENV="${KAFKA_ENV:-pre}"
export POD_NAME="${POD_NAME:-local-agent-server-${USER:-unknown}}"
export KAFKA_URL="${KAFKA_URL:-kafka-portal.pyromind.ai:31011,kafka-portal.pyromind.ai:31012,kafka-portal.pyromind.ai:31013}"

# Allow all CORS origins in development
export OH_ALLOW_CORS_ORIGIN_REGEX="${OH_ALLOW_CORS_ORIGIN_REGEX:-https?://.+}"

# ----------------------------------------------------------
# Workspace
# ----------------------------------------------------------
# Deployment may set lowercase workspace_dir. Keep uppercase WORKSPACE_DIR as a
# convenience alias, then derive the Config fields the server actually consumes.
export workspace_dir="${workspace_dir:-${WORKSPACE_DIR:-${SOFTWARE_AGENT_SDK_DIR}/workspace}}"
export WORKSPACE_DIR="${workspace_dir}"
export OPENHANDS_CONFIG_DIR="${OPENHANDS_CONFIG_DIR:-${WORKSPACE_DIR}}"
export OPENHANDS_AGENT_SERVER_CONFIG_PATH="${OPENHANDS_AGENT_SERVER_CONFIG_PATH:-${OPENHANDS_CONFIG_DIR}/openhands_agent_server_config.json}"
export OH_CONVERSATIONS_PATH="${OH_CONVERSATIONS_PATH:-${WORKSPACE_DIR}/conversations}"
export OH_WORKSPACE_PATH="${OH_WORKSPACE_PATH:-${WORKSPACE_DIR}/project}"
export OH_BASH_EVENTS_DIR="${OH_BASH_EVENTS_DIR:-${WORKSPACE_DIR}/bash_events}"
mkdir -p \
  "${OPENHANDS_CONFIG_DIR}" \
  "${OH_CONVERSATIONS_PATH}" \
  "${OH_WORKSPACE_PATH}" \
  "${OH_BASH_EVENTS_DIR}"

# ----------------------------------------------------------
# Pyromind Knowledge Base
# ----------------------------------------------------------
# Points to the knowledge/ folder in this repository by default.
export PYROMIND_KNOWLEDGE_BASE_PATH="${PYROMIND_KNOWLEDGE_BASE_PATH:-${SOFTWARE_AGENT_SDK_DIR}/knowledge}"
export PYROMIND_SKILLS_PATH="${PYROMIND_SKILLS_PATH:-${SOFTWARE_AGENT_SDK_DIR}/.agents/skills}"

for required_dir in basic jupyterlab nodes sdk studio; do
  if [[ ! -d "${PYROMIND_KNOWLEDGE_BASE_PATH}/${required_dir}" ]]; then
    echo "ERROR: knowledge directory missing: ${PYROMIND_KNOWLEDGE_BASE_PATH}/${required_dir}" >&2
    exit 1
  fi
done

if [[ ! -f "${PYROMIND_KNOWLEDGE_BASE_PATH}/dataset_processing_workflow.py" ]]; then
  echo "ERROR: knowledge workflow example missing: ${PYROMIND_KNOWLEDGE_BASE_PATH}/dataset_processing_workflow.py" >&2
  exit 1
fi

if [[ ! -f "${PYROMIND_SKILLS_PATH}/generate-workflow-dsl/SKILL.md" ]]; then
  echo "ERROR: workflow DSL skill missing: ${PYROMIND_SKILLS_PATH}/generate-workflow-dsl/SKILL.md" >&2
  exit 1
fi

if [ -x "${HOME}/.local/bin/uv" ]; then
  export PATH="${HOME}/.local/bin:${PATH}"
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "Error: uv is required but was not found in PATH." >&2
  echo "Install it with: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  exit 127
fi
export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/software-agent-sdk-uv-cache}"
mkdir -p "${UV_CACHE_DIR}"

# ----------------------------------------------------------
# Start Agent Server
# ----------------------------------------------------------
cd "${SOFTWARE_AGENT_SDK_DIR}"

echo "============================================"
echo " Pyromind Agent Server"
echo "============================================"
echo " LLM Base URL:      ${LLM_BASE_URL}"
echo " Server root:       ${SOFTWARE_AGENT_SDK_DIR}"
echo " Knowledge Base:    ${PYROMIND_KNOWLEDGE_BASE_PATH}"
echo " Skills:            ${PYROMIND_SKILLS_PATH}"
echo " Workspace root:    ${WORKSPACE_DIR}"
echo " Conversations:     ${OH_CONVERSATIONS_PATH}"
echo " Project workspace: ${OH_WORKSPACE_PATH}"
echo " Bash events:       ${OH_BASH_EVENTS_DIR}"
echo " Config path:       ${OPENHANDS_AGENT_SERVER_CONFIG_PATH}"
echo " Pyromind JWT auth: ${OH_ENABLE_PYROMIND_JWT_AUTH}"
echo " Server env:        ${APP_ENV:-dev}"
echo " Kafka env:         ${KAFKA_ENV}"
echo " Kafka brokers:     ${KAFKA_URL:-cluster-internal default}"
echo " Kafka group:       ${POD_NAME}"
echo " Session API key:   $([[ -n "${SESSION_API_KEY}" ]] && echo configured || echo disabled)"
echo " Host:              127.0.0.1"
echo " Port:              8000"
echo " Auto-reload:       enabled"
echo "============================================"
echo ""

uv run python -m openhands.agent_server \
  --host 127.0.0.1 \
  --port 8000 \
  --reload
