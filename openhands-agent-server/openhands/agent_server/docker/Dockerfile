# syntax=docker/dockerfile:1.7

# NOTE: LC_ALL/LANG must be set to C.UTF-8 for libtmux to work correctly with
# PyInstaller builds. Without proper locale, tmux converts UTF-8 separator
# characters to underscores, breaking libtmux's format parsing.
ARG BASE_IMAGE=nikolaik/python-nodejs:python3.13-nodejs22-slim
ARG USERNAME=openhands
ARG UID=10001
ARG GID=10001
ARG PORT=8000

####################################################################################
# Builder (source mode)
# We copy source + build a venv here for local dev and debugging.
#
# NOTE: We use the base image's system Python (--python-preference only-system)
# instead of uv-managed python-build-standalone. The python-build-standalone
# builds ship libpython with an executable stack (PT_GNU_STACK PF_X).
# Docker containers running under seccomp restrictions (e.g. GitHub Actions
# Docker-in-Docker) reject dlopen() on such libraries with:
#   "cannot enable executable stack as shared object requires: Invalid argument"
# Debian's CPython packages do not have this issue.
#
# PORTABILITY: After the venv is built we bundle the interpreter, stdlib,
# and libpython into /agent-server/.python/ and repoint the venv at it.
# This makes /agent-server fully self-contained — downstream consumers
# (e.g. eval images) can COPY it onto any base image without needing a
# compatible system Python.
####################################################################################
FROM --platform=$TARGETPLATFORM python:3.13-bookworm AS builder
ARG USERNAME UID GID
ENV UV_PROJECT_ENVIRONMENT=/agent-server/.venv

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
    uv venv --python-preference only-system .venv && uv sync --frozen --no-editable --extra boto3

# Bundle the Python runtime inside /agent-server so that the entire directory
# is self-contained and portable.  Eval images (and any other consumer) can
# COPY /agent-server onto *any* base image without requiring that base image
# to ship a compatible system Python.
#
# What we copy:
#   .python/bin/python3.13     – the interpreter binary
#   .python/lib/python3.13/    – the standard library (minus tests)
#   .python/lib/libpython*.so* – shared libraries (Debian builds --enable-shared)
#
# We then repoint the venv's symlinks and pyvenv.cfg at the bundled copy.
RUN set -eux; \
    REAL_PYTHON=$(readlink -f .venv/bin/python3); \
    PY_VER=$("${REAL_PYTHON}" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"); \
    PYTHON_PREFIX=$("${REAL_PYTHON}" -c "import sys; print(sys.base_prefix)"); \
    # --- copy interpreter binary ------------------------------------------------- \
    mkdir -p .python/bin; \
    cp "${REAL_PYTHON}" ".python/bin/python${PY_VER}"; \
    ln -s "python${PY_VER}" .python/bin/python3; \
    ln -s "python${PY_VER}" .python/bin/python; \
    # --- copy standard library (skip test suite to save ~30 MB) ------------------ \
    mkdir -p .python/lib; \
    cp -a "${PYTHON_PREFIX}/lib/python${PY_VER}" ".python/lib/python${PY_VER}"; \
    rm -rf ".python/lib/python${PY_VER}/test" \
           ".python/lib/python${PY_VER}/tests" \
           ".python/lib/python${PY_VER}/idle_test" \
           ".python/lib/python${PY_VER}/idlelib"; \
    # --- copy shared libraries (libpython) --------------------------------------- \
    for lib in "${PYTHON_PREFIX}"/lib/libpython*.so*; do \
        [ -e "$lib" ] && cp -a "$lib" .python/lib/; \
    done; \
    # --- repoint venv at the bundled Python -------------------------------------- \
    for f in .venv/bin/python*; do \
        [ -L "$f" ] || continue; \
        name=$(basename "$f"); \
        rm "$f"; \
        ln -s "../../.python/bin/${name}" "$f"; \
    done; \
    # Ensure canonical names resolve (some venvs only create python3 + python) \
    [ -L .venv/bin/python ] || ln -s "../../.python/bin/python" .venv/bin/python; \
    [ -L .venv/bin/python3 ] || ln -s "../../.python/bin/python3" .venv/bin/python3; \
    sed -i "s|^home = .*|home = /agent-server/.python/bin|" .venv/pyvenv.cfg; \
    # --- quick smoke-test inside the builder ------------------------------------- \
    .venv/bin/python -c "import sys; print('bundled python:', sys.executable, sys.version)"

####################################################################################
# Binary Builder (binary mode)
# We run pyinstaller here to produce openhands-agent-server
####################################################################################
FROM builder AS binary-builder
ARG USERNAME UID GID

# We need --dev for pyinstaller
RUN --mount=type=cache,target=/home/${USERNAME}/.cache,uid=${UID},gid=${GID} \
    uv sync --frozen --dev --no-editable --extra boto3

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


ARG OPENHANDS_BUILD_GIT_SHA=unknown
ARG OPENHANDS_BUILD_GIT_REF=unknown
ENV OPENHANDS_BUILD_GIT_SHA=${OPENHANDS_BUILD_GIT_SHA}
ENV OPENHANDS_BUILD_GIT_REF=${OPENHANDS_BUILD_GIT_REF}

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

# Pre-install ACP servers for ACPAgent support (Claude Code, Codex, Gemini CLI)
# Install Node.js 22 to a dedicated prefix so ACP packages get a modern runtime
# WITHOUT overwriting the repo-specific Node.js that test suites depend on.
# SWE-bench images ship NVM/apt-managed Node 8-14 which cannot run ACP packages.
ENV ACP_NODE_DIR=/opt/acp-node
RUN set -eux; \
    ARCH=$(dpkg --print-architecture); \
    case "$ARCH" in \
      amd64) NARCH=x64; NODE_SHA256=69b09dba5c8dcb05c4e4273a4340db1005abeafe3927efda2bc5b249e80437ec;; \
      arm64) NARCH=arm64; NODE_SHA256=08bfbf538bad0e8cbb0269f0173cca28d705874a67a22f60b57d99dc99e30050;; \
      *) echo "Unsupported architecture for ACP Node install: $ARCH" >&2; exit 1;; \
    esac; \
    NODE_TARBALL="/tmp/node-v22.14.0-linux-${NARCH}.tar.xz"; \
    mkdir -p "$ACP_NODE_DIR"; \
    curl -fsSL "https://nodejs.org/dist/v22.14.0/node-v22.14.0-linux-${NARCH}.tar.xz" -o "$NODE_TARBALL"; \
    echo "$NODE_SHA256  $NODE_TARBALL" | sha256sum -c -; \
    tar -xJ --strip-components=1 -C "$ACP_NODE_DIR" -f "$NODE_TARBALL"; \
    rm -f "$NODE_TARBALL"; \
    PATH="$ACP_NODE_DIR/bin:$PATH"; \
    node --version; \
    npm install -g \
      @zed-industries/claude-agent-acp \
      @zed-industries/codex-acp \
      @google/gemini-cli; \
    # Create wrappers in /usr/local/bin that prepend ACP's Node 22 to PATH.
    # This ensures the ACP binary's #!/usr/bin/env node shebang resolves to
    # Node 22, while the repo's own node (NVM/system) stays untouched for tests.
    for bin in claude-agent-acp codex-acp gemini; do \
      if [ -e "$ACP_NODE_DIR/bin/$bin" ]; then \
        printf '#!/bin/sh\nPATH="%s/bin:$PATH" exec "%s/bin/%s" "$@"\n' \
          "$ACP_NODE_DIR" "$ACP_NODE_DIR" "$bin" \
          > /usr/local/bin/"$bin"; \
        chmod +x /usr/local/bin/"$bin"; \
      fi; \
    done

# Configure Claude Code managed settings for headless operation:
# Allow all tool permissions (no human in the loop to approve).
RUN mkdir -p /etc/claude-code && \
    echo '{"permissions":{"allow":["Edit","Read","Bash"]}}' > /etc/claude-code/managed-settings.json

# NOTE: we should NOT include UV_PROJECT_ENVIRONMENT here,
# since the agent might use it to perform other work (e.g. tools that use Python)
COPY --from=ghcr.io/astral-sh/uv /uv /uvx /bin/

USER ${USERNAME}
WORKDIR /
# Locale settings required for libtmux to work with PyInstaller builds
ENV LC_ALL=C.UTF-8
ENV LANG=C.UTF-8
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
COPY --chown=${USERNAME}:${USERNAME} --from=builder /agent-server /agent-server
# Bundled Python's libpython*.so lives under /agent-server/.python/lib
ENV LD_LIBRARY_PATH=/agent-server/.python/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
ENTRYPOINT ["/agent-server/.venv/bin/python", "-m", "openhands.agent_server"]

FROM base-image-minimal AS source-minimal
ARG USERNAME
COPY --chown=${USERNAME}:${USERNAME} --from=builder /agent-server /agent-server
ENV LD_LIBRARY_PATH=/agent-server/.python/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
ENTRYPOINT ["/agent-server/.venv/bin/python", "-m", "openhands.agent_server"]

############################
# Target A.1: portability-test
# Verifies that /agent-server stays runnable when copied onto a base image
# with no Python installed, matching the benchmarks repo assembly pattern.
############################
FROM debian:bookworm-slim AS portability-test
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /agent-server /agent-server
ENV LD_LIBRARY_PATH=/agent-server/.python/lib
RUN set -eux; \
    test -x /agent-server/.python/bin/python3; \
    test -f /agent-server/.venv/pyvenv.cfg; \
    /agent-server/.venv/bin/python -c "import sys; print('OK:', sys.executable, sys.version)"; \
    /agent-server/.venv/bin/python -c "import openhands.agent_server; print('agent_server importable')"

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
