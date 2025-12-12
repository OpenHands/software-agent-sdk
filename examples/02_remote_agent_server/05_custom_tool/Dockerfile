# Dockerfile for custom agent server with custom tools
# This Dockerfile builds the agent server from source and includes custom tools
#
# Usage:
#   docker build -t my-custom-agent-server:latest \
#     -f examples/02_remote_agent_server/05_custom_tool/Dockerfile \
#     .

# Build the agent server from source
# This uses the main Dockerfile in openhands-agent-server/openhands/agent_server/docker/Dockerfile
ARG USERNAME=openhands
ARG UID=10001
ARG GID=10001

FROM python:3.12-bullseye AS builder
ARG USERNAME UID GID
ENV UV_PROJECT_ENVIRONMENT=/agent-server/.venv
ENV UV_PYTHON_INSTALL_DIR=/agent-server/uv-managed-python

COPY --from=ghcr.io/astral-sh/uv /uv /uvx /bin/

RUN groupadd -g ${GID} ${USERNAME} \
 && useradd -m -u ${UID} -g ${GID} -s /usr/sbin/nologin ${USERNAME}
USER ${USERNAME}
WORKDIR /agent-server
# Cache-friendly: lockfiles first
COPY --chown=${USERNAME}:${USERNAME} pyproject.toml uv.lock README.md LICENSE ./
COPY --chown=${USERNAME}:${USERNAME} openhands-sdk ./openhands-sdk
COPY --chown=${USERNAME}:${USERNAME} openhands-tools ./openhands-tools
COPY --chown=${USERNAME}:${USERNAME} openhands-workspace ./openhands-workspace
COPY --chown=${USERNAME}:${USERNAME} openhands-agent-server ./openhands-agent-server
RUN --mount=type=cache,target=/home/${USERNAME}/.cache,uid=${UID},gid=${GID} \
    uv python install 3.12 && uv venv --python 3.12 .venv && uv sync --frozen --no-editable --managed-python

# Runtime stage
FROM nikolaik/python-nodejs:python3.12-nodejs22 AS runtime
ARG USERNAME UID GID
ARG PORT=8000

# Install base packages and create user
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates curl wget sudo apt-utils git jq tmux build-essential \
        coreutils util-linux procps findutils grep sed; \
    \
    (getent group ${GID} || groupadd -g ${GID} ${USERNAME}); \
    (id -u ${USERNAME} >/dev/null 2>&1 || useradd -m -u ${UID} -g ${GID} -s /bin/bash ${USERNAME}); \
    usermod -aG sudo ${USERNAME}; \
    echo "${USERNAME} ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers; \
    mkdir -p /workspace/project; \
    chown -R ${USERNAME}:${USERNAME} /workspace; \
    rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv /uv /uvx /bin/

USER ${USERNAME}
WORKDIR /
ENV OH_ENABLE_VNC=false
ENV LOG_JSON=true
ENV UV_PYTHON_INSTALL_DIR=/agent-server/uv-managed-python

# Copy the built agent server from the builder stage
COPY --chown=${USERNAME}:${USERNAME} --from=builder /agent-server /agent-server

# Copy custom tools into the Python path
COPY --chown=${USERNAME}:${USERNAME} examples/02_remote_agent_server/05_custom_tool/custom_tools /app/custom_tools

# Add /app to PYTHONPATH so custom_tools can be imported
ENV PYTHONPATH="/app:${PYTHONPATH}"

EXPOSE ${PORT}
ENTRYPOINT ["/agent-server/.venv/bin/python", "-m", "openhands.agent_server"]
