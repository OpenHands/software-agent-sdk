# syntax=docker/dockerfile:1.7

# NOTE: Using Python 3.12 due to PyInstaller+libtmux compatibility issue
# with Python 3.13. See issue #1886 for details.
ARG BASE_IMAGE=nikolaik/python-nodejs:python3.12-nodejs22
ARG USERNAME=openhands
ARG UID=10001
ARG GID=10001
ARG PORT=8000

####################################################################################
# Builder (source mode)
# We copy source + build a venv here for local dev and debugging.
####################################################################################
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

####################################################################################
# Binary Builder (binary mode)
# We run pyinstaller here to produce openhands-agent-server
####################################################################################
FROM builder AS binary-builder
ARG USERNAME UID GID

# We need --dev for pyinstaller
RUN --mount=type=cache,target=/home/${USERNAME}/.cache,uid=${UID},gid=${GID} \
    uv sync --frozen --dev --no-editable

RUN --mount=type=cache,target=/home/${USERNAME}/.cache,uid=${UID},gid=${GID} \
    uv run pyinstaller openhands-agent-server/openhands/agent_server/agent-server.spec
# Fail fast if the expected binary is missing
RUN test -x /agent-server/dist/openhands-agent-server

####################################################################################
# Base image (minimal)
# It includes only basic packages and the UV runtime.
# No Docker, no VNC, no Desktop, no VSCode Web.
# Suitable for running in headless/evaluation mode.
####################################################################################
FROM ${BASE_IMAGE} AS base-image-minimal
ARG USERNAME UID GID PORT

# Install base packages and create user
RUN set -eux; \
    # Install base packages (works for both Debian-based images)
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates curl wget sudo apt-utils git jq tmux build-essential \
        coreutils util-linux procps findutils grep sed \
        # Docker dependencies
        apt-transport-https gnupg lsb-release; \
    \
    # Create user and group
    (getent group ${GID} || groupadd -g ${GID} ${USERNAME}); \
    (id -u ${USERNAME} >/dev/null 2>&1 || useradd -m -u ${UID} -g ${GID} -s /bin/bash ${USERNAME}); \
    # Add user to sudo group
    usermod -aG sudo ${USERNAME}; \
    echo "${USERNAME} ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers; \
    # Create workspace directory
    mkdir -p /workspace/project; \
    chown -R ${USERNAME}:${USERNAME} /workspace; \
    rm -rf /var/lib/apt/lists/*

# NOTE: we should NOT include UV_PROJECT_ENVIRONMENT here, 
# since the agent might use it to perform other work (e.g. tools that use Python)
COPY --from=ghcr.io/astral-sh/uv /uv /uvx /bin/

USER ${USERNAME}
WORKDIR /
ENV OH_ENABLE_VNC=false
ENV LOG_JSON=true
EXPOSE ${PORT}

####################################################################################
# Base image (full)
# It includes additional Docker, VNC, Desktop, and VSCode Web.
####################################################################################
FROM base-image-minimal AS base-image
ARG USERNAME

USER root
# --- VSCode Web ---
ENV EDITOR=code \
    VISUAL=code \
    GIT_EDITOR="code --wait" \
    OPENVSCODE_SERVER_ROOT=/openhands/.openvscode-server
ARG RELEASE_TAG="openvscode-server-v1.98.2"
ARG RELEASE_ORG="gitpod-io"
RUN set -eux; \
    # Create necessary directories
    mkdir -p $(dirname ${OPENVSCODE_SERVER_ROOT}); \
    \
    # Determine architecture
    arch=$(uname -m); \
    if [ "${arch}" = "x86_64" ]; then \
        arch="x64"; \
    elif [ "${arch}" = "aarch64" ]; then \
        arch="arm64"; \
    elif [ "${arch}" = "armv7l" ]; then \
        arch="armhf"; \
    fi; \
    \
    # Download and install VSCode Server
    wget https://github.com/${RELEASE_ORG}/openvscode-server/releases/download/${RELEASE_TAG}/${RELEASE_TAG}-linux-${arch}.tar.gz; \
    tar -xzf ${RELEASE_TAG}-linux-${arch}.tar.gz; \
    if [ -d "${OPENVSCODE_SERVER_ROOT}" ]; then rm -rf "${OPENVSCODE_SERVER_ROOT}"; fi; \
    mv ${RELEASE_TAG}-linux-${arch} ${OPENVSCODE_SERVER_ROOT}; \
    cp ${OPENVSCODE_SERVER_ROOT}/bin/remote-cli/openvscode-server ${OPENVSCODE_SERVER_ROOT}/bin/remote-cli/code; \
    rm -f ${RELEASE_TAG}-linux-${arch}.tar.gz; \
    \
    # Set proper ownership
    chown -R ${USERNAME}:${USERNAME} ${OPENVSCODE_SERVER_ROOT}


# Include VSCode extensions alongside the server so targets inheriting base-image
# implicitly get the extensions; minimal images (without VSCode) won't.
COPY --chown=${USERNAME}:${USERNAME} --from=builder /agent-server/openhands-agent-server/openhands/agent_server/vscode_extensions ${OPENVSCODE_SERVER_ROOT}/extensions

# --- Docker ---
RUN set -eux; \
    # Determine OS type and install Docker accordingly
    if grep -q "ubuntu" /etc/os-release; then \
        # Handle Ubuntu
        install -m 0755 -d /etc/apt/keyrings; \
        curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc; \
        chmod a+r /etc/apt/keyrings/docker.asc; \
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null; \
    else \
        # Handle Debian
        install -m 0755 -d /etc/apt/keyrings; \
        curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc; \
        chmod a+r /etc/apt/keyrings/docker.asc; \
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null; \
    fi; \
    # Install Docker Engine, containerd, and Docker Compose
    apt-get update; \
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*

# Configure Docker daemon with MTU 1450 to prevent packet fragmentation issues
RUN mkdir -p /etc/docker && \
    echo '{"mtu": 1450}' > /etc/docker/daemon.json

# --- GitHub CLI ---
RUN set -eux; \
    mkdir -p -m 755 /etc/apt/keyrings; \
    wget -nv -O /etc/apt/keyrings/githubcli-archive-keyring.gpg \
        https://cli.github.com/packages/githubcli-archive-keyring.gpg; \
    chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg; \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list; \
    apt-get update; \
    apt-get install -y gh; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*

# --- VNC + Desktop + noVNC ---
RUN set -eux; \
  apt-get update; \
  apt-get install -y --no-install-recommends \
    # GUI bits (remove entirely if headless)
    tigervnc-standalone-server xfce4 dbus-x11 novnc websockify \
    # Browser
    $(if grep -q "ubuntu" /etc/os-release; then echo "chromium-browser"; else echo "chromium"; fi); \
  apt-get clean; rm -rf /var/lib/apt/lists/*

ENV NOVNC_WEB=/usr/share/novnc \
    NOVNC_PORT=8002 \
    DISPLAY=:1 \
    VNC_GEOMETRY=1280x800 \
    CHROME_BIN=/usr/bin/chromium \
    PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium \
    CHROMIUM_FLAGS="--no-sandbox --disable-dev-shm-usage --disable-gpu"

RUN chown -R ${USERNAME}:${USERNAME} ${NOVNC_WEB}
# Override default XFCE wallpaper
COPY --chown=${USERNAME}:${USERNAME} openhands-agent-server/openhands/agent_server/docker/wallpaper.svg /usr/share/backgrounds/xfce/xfce-shapes.svg

USER ${USERNAME}
WORKDIR /
ENV OH_ENABLE_VNC=false
ENV LOG_JSON=true
EXPOSE ${PORT} ${NOVNC_PORT}


####################################################################################
####################################################################################
# Build Targets
####################################################################################
####################################################################################

############################
# Target A: source
# Local dev and debugging mode: copy source + venv from builder
############################
FROM base-image AS source
ARG USERNAME
ENV UV_PYTHON_INSTALL_DIR=/agent-server/uv-managed-python
COPY --chown=${USERNAME}:${USERNAME} --from=builder /agent-server /agent-server
ENTRYPOINT ["/agent-server/.venv/bin/python", "-m", "openhands.agent_server"]

FROM base-image-minimal AS source-minimal
ARG USERNAME
ENV UV_PYTHON_INSTALL_DIR=/agent-server/uv-managed-python
COPY --chown=${USERNAME}:${USERNAME} --from=builder /agent-server /agent-server
ENTRYPOINT ["/agent-server/.venv/bin/python", "-m", "openhands.agent_server"]

############################
# Target B: binary-runtime
# Production mode: build the binary inside Docker and copy it in.
# NOTE: no support for external artifact contexts anymore.
############################
FROM base-image AS binary
ARG USERNAME

COPY --chown=${USERNAME}:${USERNAME} --from=binary-builder /agent-server/dist/openhands-agent-server /usr/local/bin/openhands-agent-server
RUN chmod +x /usr/local/bin/openhands-agent-server
# Fix library path to use system GCC libraries instead of bundled ones
ENV LD_LIBRARY_PATH=/usr/lib/aarch64-linux-gnu:/usr/lib:/usr/lib/x86_64-linux-gnu:$LD_LIBRARY_PATH
ENTRYPOINT ["/usr/local/bin/openhands-agent-server"]

FROM base-image-minimal AS binary-minimal
ARG USERNAME
COPY --chown=${USERNAME}:${USERNAME} --from=binary-builder /agent-server/dist/openhands-agent-server /usr/local/bin/openhands-agent-server
RUN chmod +x /usr/local/bin/openhands-agent-server
# Fix library path to use system GCC libraries instead of bundled ones
ENV LD_LIBRARY_PATH=/usr/lib/aarch64-linux-gnu:/usr/lib:/usr/lib/x86_64-linux-gnu:$LD_LIBRARY_PATH
ENTRYPOINT ["/usr/local/bin/openhands-agent-server"]
