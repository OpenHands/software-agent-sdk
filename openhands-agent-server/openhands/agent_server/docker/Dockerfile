# syntax=docker/dockerfile:1.7

# NOTE: LC_ALL/LANG must be set to C.UTF-8 for libtmux to work correctly with
# PyInstaller builds. Without proper locale, tmux converts UTF-8 separator
# characters to underscores, breaking libtmux's format parsing.
ARG BASE_IMAGE=nikolaik/python-nodejs:python3.13-nodejs22-slim
ARG USERNAME=openhands
ARG UID=10001
ARG GID=10001
ARG PORT=8000
# Opt-in build flag for the Vertex AI extra (`openhands-sdk[vertex]`). Off by
# default to keep the published image lean. Pass `--build-arg ENABLE_VERTEX=1`
# to bundle google-cloud-aiplatform so the resulting binary supports
# `vertex_ai/*` partner models (MiniMax, Qwen, Kimi MaaS endpoints).
ARG ENABLE_VERTEX=0
# Comma-separated ACP provider keys (see ACP_PROVIDERS in
# openhands-sdk/openhands/sdk/settings/acp_providers.py) to bake into the
# acp-providers stage below. The default reproduces the full provider set
# shipped today; an unknown key fails the build rather than silently
# installing nothing.
ARG INSTALL_ACP_PROVIDERS=claude-code,codex,gemini-cli
# Comma-separated capability keys to include in the `base-image` stage below
# (`base-image-minimal` is unaffected). The default reproduces today's full
# contents; an unknown key fails the build. The GitHub CLI is expected to
# join this list later (see OpenHands/software-agent-sdk#4645) — the parsing
# below is written so that's a one-case addition, not a signature change.
ARG INSTALL_CAPABILITIES=vscode,browser,desktop,docker

####################################################################################
# Builder (source mode)
# We copy source + build a venv here for local dev and debugging.
#
# SELF-CONTAINED /agent-server CONTRACT:
# uv installs python-build-standalone into /agent-server/uv-managed-python and
# creates .venv against it. Both live under /agent-server, so downstream
# consumers can COPY /agent-server onto any base image and the venv works.
#
# uv >= 0.11.5 pulls python-build-standalone >= 20260408, which ships
# libpython without PT_GNU_STACK PF_X (executable stack). Earlier releases
# had this flag set due to LLVM/BOLT bugs, causing glibc >= 2.41 and
# DinD/sysbox/seccomp to reject dlopen() with "cannot enable executable
# stack". No sanitizer or workaround is needed on fixed releases.
# See OpenHands/software-agent-sdk#2761.
####################################################################################
FROM python:3.13-bookworm AS builder
ARG USERNAME UID GID ENABLE_VERTEX
ENV UV_PROJECT_ENVIRONMENT=/agent-server/.venv
ENV UV_PYTHON_INSTALL_DIR=/agent-server/uv-managed-python

# uv 0.11.5+ embeds python-build-standalone 20260408 metadata, which is the
# first release with the PT_GNU_STACK fix. Pin to 0.11.6 (latest at time of
# writing) rather than :latest so builds are reproducible.
COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /uvx /bin/

RUN groupadd -g ${GID} ${USERNAME} \
 && useradd -m -u ${UID} -g ${GID} -s /usr/sbin/nologin ${USERNAME} \
 && mkdir -p /agent-server/uv-managed-python \
 && chown -R ${USERNAME}:${USERNAME} /agent-server
USER ${USERNAME}
WORKDIR /agent-server
# Cache-friendly: lockfiles first
COPY --chown=${USERNAME}:${USERNAME} pyproject.toml uv.lock README.md LICENSE ./
COPY --chown=${USERNAME}:${USERNAME} openhands-sdk ./openhands-sdk
COPY --chown=${USERNAME}:${USERNAME} openhands-tools ./openhands-tools
COPY --chown=${USERNAME}:${USERNAME} openhands-workspace ./openhands-workspace
COPY --chown=${USERNAME}:${USERNAME} openhands-agent-server ./openhands-agent-server
RUN --mount=type=cache,target=/home/${USERNAME}/.cache,uid=${UID},gid=${GID} \
    EXTRA_FLAGS=""; \
    if [ "$ENABLE_VERTEX" = "1" ]; then EXTRA_FLAGS="--extra vertex"; fi; \
    uv python install 3.13 && \
    uv venv --python-preference only-managed --python 3.13 .venv && \
    uv sync --frozen --no-editable --managed-python --extra boto3 $EXTRA_FLAGS && \
    readlink -f .venv/bin/python | grep -q '^/agent-server/uv-managed-python/'

####################################################################################
# Binary Builder (binary mode)
# We run pyinstaller here to produce openhands-agent-server
####################################################################################
FROM builder AS binary-builder
ARG USERNAME UID GID ENABLE_VERTEX

# We need --dev for pyinstaller
RUN --mount=type=cache,target=/home/${USERNAME}/.cache,uid=${UID},gid=${GID} \
    EXTRA_FLAGS=""; \
    if [ "$ENABLE_VERTEX" = "1" ]; then EXTRA_FLAGS="--extra vertex"; fi; \
    uv sync --frozen --dev --no-editable --extra boto3 $EXTRA_FLAGS

RUN --mount=type=cache,target=/home/${USERNAME}/.cache,uid=${UID},gid=${GID} \
    uv run pyinstaller openhands-agent-server/openhands/agent_server/agent-server.spec
# Fail fast if the expected binary is missing
RUN test -x /agent-server/dist/openhands-agent-server

####################################################################################
# ACP providers (parent-independent)
#
# Built from a fixed base rather than ${BASE_IMAGE} so that COPY --from=acp-providers
# below produces the exact same compressed layer for every base-image-minimal build,
# regardless of which BASE_IMAGE it lands on. This is what lets the ~600MB provider
# payload be shared across per-instance evaluation image builds instead of being
# reinstalled (and re-uploaded) once per instance.
#
# Failures here are fatal: there is no per-BASE_IMAGE fallback for a broken build of
# the shared payload itself (see the compatibility probe in base-image-minimal for
# the per-target-image fallback).
####################################################################################
FROM python:3.13-bookworm AS acp-providers
ARG INSTALL_ACP_PROVIDERS
# A fresh top-level path, not nested under a directory (e.g. /opt) that base
# images commonly already populate (SWE-bench images ship /opt/miniconda3):
# COPY --from=<stage> only produces a byte-identical layer across different
# BASE_IMAGE parents when the destination itself doesn't already exist in the
# parent, mirroring /agent-server's own top-level placement in the builder
# stage above.
ENV ACP_NODE_DIR=/acp-node
RUN set -eux; \
    ACP_WRAPPER_DIR=/opt/acp-wrappers; \
    mkdir -p "$ACP_NODE_DIR" "$ACP_WRAPPER_DIR"; \
    PACKAGES=""; \
    WRAPPER_BINS=""; \
    for provider in $(echo "$INSTALL_ACP_PROVIDERS" | tr ',' ' '); do \
      case "$provider" in \
        claude-code) PACKAGES="$PACKAGES @agentclientprotocol/claude-agent-acp@0.63.0"; \
          WRAPPER_BINS="$WRAPPER_BINS claude-agent-acp";; \
        codex) PACKAGES="$PACKAGES @agentclientprotocol/codex-acp@1.1.7"; \
          WRAPPER_BINS="$WRAPPER_BINS codex-acp";; \
        gemini-cli) PACKAGES="$PACKAGES @google/gemini-cli@0.46.0"; \
          WRAPPER_BINS="$WRAPPER_BINS gemini";; \
        *) echo "Unknown ACP provider '$provider' in INSTALL_ACP_PROVIDERS" \
             "(expected one of: claude-code, codex, gemini-cli)" >&2; exit 1;; \
      esac; \
    done; \
    if [ -z "$PACKAGES" ]; then \
      echo "INSTALL_ACP_PROVIDERS is empty; no ACP providers will be installed"; \
      exit 0; \
    fi; \
    apt-get -o Acquire::Retries=5 update; \
    apt-get -o Acquire::Retries=5 install -y --no-install-recommends \
        ca-certificates curl xz-utils; \
    rm -rf /var/lib/apt/lists/*; \
    ARCH=$(uname -m); \
    case "$ARCH" in \
      x86_64|amd64) NARCH=x64; NODE_SHA256=69b09dba5c8dcb05c4e4273a4340db1005abeafe3927efda2bc5b249e80437ec;; \
      aarch64|arm64) NARCH=arm64; NODE_SHA256=08bfbf538bad0e8cbb0269f0173cca28d705874a67a22f60b57d99dc99e30050;; \
      *) echo "Unsupported architecture for ACP providers: '$ARCH'" >&2; exit 1;; \
    esac; \
    NODE_TARBALL="/tmp/node-v22.14.0-linux-${NARCH}.tar.xz"; \
    curl -fsSL --retry 5 --retry-delay 2 --retry-connrefused \
        "https://nodejs.org/dist/v22.14.0/node-v22.14.0-linux-${NARCH}.tar.xz" -o "$NODE_TARBALL"; \
    echo "$NODE_SHA256  $NODE_TARBALL" | sha256sum -c -; \
    tar -xJ --strip-components=1 -C "$ACP_NODE_DIR" -f "$NODE_TARBALL"; \
    rm -f "$NODE_TARBALL"; \
    "$ACP_NODE_DIR/bin/node" --version; \
    PATH="$ACP_NODE_DIR/bin:$PATH" "$ACP_NODE_DIR/bin/npm" install -g $PACKAGES; \
    for bin in $WRAPPER_BINS; do \
      printf '#!/bin/sh\nPATH="%s/bin:$PATH" exec "%s/bin/%s" "$@"\n' \
        "$ACP_NODE_DIR" "$ACP_NODE_DIR" "$bin" \
        > "$ACP_WRAPPER_DIR/$bin"; \
      chmod +x "$ACP_WRAPPER_DIR/$bin"; \
    done

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
    # Install base packages across the most common package managers, since
    # benchmark base images aren't always Debian-based. `tini` is added on
    # apt/apk where it's reliably available; on the other paths the kernel-
    # reaping behaviour falls back to dumb-init's absence (the agent server
    # is short-lived enough on non-Debian images that PID 1 zombie reaping
    # has not been observed to matter — revisit if it does).
    if command -v apt-get >/dev/null 2>&1; then \
        apt-get -o Acquire::Retries=5 update; \
        apt-get -o Acquire::Retries=5 install -y --no-install-recommends \
            bash ca-certificates curl wget sudo apt-utils git jq tmux tar \
            build-essential coreutils util-linux procps findutils grep sed \
            tini apt-transport-https gnupg lsb-release xz-utils; \
        rm -rf /var/lib/apt/lists/*; \
    elif command -v apk >/dev/null 2>&1; then \
        apk add --no-cache \
            bash ca-certificates curl wget sudo git jq tmux tar build-base \
            coreutils util-linux procps findutils grep sed tini gnupg shadow xz; \
    elif command -v microdnf >/dev/null 2>&1; then \
        microdnf install -y \
            bash ca-certificates curl wget sudo git jq tmux tar make gcc gcc-c++ \
            coreutils util-linux procps-ng findutils grep sed shadow-utils \
            gnupg2 xz; \
        microdnf clean all; \
    elif command -v dnf >/dev/null 2>&1; then \
        dnf install -y \
            bash ca-certificates curl wget sudo git jq tmux tar make gcc gcc-c++ \
            coreutils util-linux procps-ng findutils grep sed shadow-utils \
            gnupg2 xz; \
        dnf clean all; \
    elif command -v yum >/dev/null 2>&1; then \
        yum install -y \
            bash ca-certificates curl wget sudo git jq tmux tar make gcc gcc-c++ \
            coreutils util-linux procps-ng findutils grep sed shadow-utils \
            gnupg2 xz; \
        yum clean all; \
    elif command -v zypper >/dev/null 2>&1; then \
        zypper --non-interactive install --no-recommends \
            bash ca-certificates curl wget sudo git jq tmux tar make gcc gcc-c++ \
            coreutils util-linux procps findutils grep sed shadow gpg2 xz; \
        zypper clean --all; \
    else \
        echo "Unsupported base image: no known package manager found" >&2; \
        exit 1; \
    fi; \
    grep -Eq "^[^:]*:[^:]*:${GID}:" /etc/group || groupadd -g "${GID}" "${USERNAME}"; \
    grep -Eq "^${USERNAME}:" /etc/passwd || \
        useradd -m -u "${UID}" -g "${GID}" -s /bin/bash "${USERNAME}"; \
    # Best-effort: add user to a sudo group when one exists (Debian-style
    # `sudo` group). On Alpine/RHEL/SUSE there is no `sudo` group by default,
    # and the NOPASSWD sudoers line below grants sudo regardless of group.
    usermod -aG sudo "${USERNAME}" 2>/dev/null || true; \
    echo "${USERNAME} ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers; \
    mkdir -p /workspace/project; \
    chown -R "${USERNAME}:${USERNAME}" /workspace

# Install ACP servers for ACPAgent support (Claude Code, Codex, Gemini CLI).
# The payload itself (Node.js 22 + the ACP npm packages, under $ACP_NODE_DIR) is
# built once in the acp-providers stage above, independent of BASE_IMAGE, and
# copied in here so the layer is byte-identical across every base image it lands
# on. Only the compatibility probe below runs against the real BASE_IMAGE.
#
# Node.js 22 lives at a dedicated prefix so ACP packages get a modern runtime
# WITHOUT overwriting the repo-specific Node.js that test suites depend on.
# SWE-bench images ship NVM/apt-managed Node 8-14 which cannot run ACP packages.
ENV ACP_NODE_DIR=/acp-node
COPY --from=acp-providers /acp-node /acp-node
COPY --from=acp-providers /opt/acp-wrappers/. /usr/local/bin/

# This probe is best-effort: SWE-Bench Pro base images come from many distros
# and some have an old glibc (or use musl) that cannot run the upstream Node 22
# glibc build. The payload above was built and sha256-verified against a fixed,
# glibc-modern stage, so a failure here means "incompatible with this specific
# BASE_IMAGE", not "the ACP install failed". When that happens we remove the
# copied payload and wrappers so the rest of the build (and non-ACP agents)
# still work, rather than leaving binaries in place that cannot execute.
RUN if ! "$ACP_NODE_DIR/bin/node" --version >/dev/null 2>&1; then \
      echo "Warning: ACP Node 22 runtime is not compatible with this base image (likely older glibc or musl libc); ACP agents will not be available" >&2; \
      rm -rf "$ACP_NODE_DIR"/*; \
      rm -f /usr/local/bin/claude-agent-acp /usr/local/bin/codex-acp /usr/local/bin/gemini; \
    fi

# Configure Claude Code managed settings for headless operation:
# Allow all tool permissions (no human in the loop to approve).
RUN mkdir -p /etc/claude-code && \
    echo '{"permissions":{"allow":["Edit","Read","Bash"]}}' > /etc/claude-code/managed-settings.json

# NOTE: we should NOT include UV_PROJECT_ENVIRONMENT here,
# since the agent might use it to perform other work (e.g. tools that use Python)
COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /uvx /bin/

USER ${USERNAME}
WORKDIR /
# Locale settings required for libtmux to work with PyInstaller builds
ENV LC_ALL=C.UTF-8
ENV LANG=C.UTF-8
ENV OH_ENABLE_VNC=false
ENV LOG_JSON=true
# The ACP CLIs run without the user's host home here, so OpenHands supplies the
# managed skill catalog they cannot read for themselves (#4019).
ENV OH_ACP_SKILL_SOURCING=openhands_managed
EXPOSE ${PORT}

####################################################################################
# Base image (full)
# It includes additional Docker, VNC, Desktop, and VSCode Web.
####################################################################################
FROM base-image-minimal AS base-image
ARG USERNAME
ARG INSTALL_CAPABILITIES

USER root

# Fail fast on a typo'd capability name before spending any time on the
# installs below (each block only checks for ITS OWN keyword's presence, so
# an unrecognized one would otherwise be silently ignored rather than erroring).
RUN set -eux; \
    for capability in $(echo "$INSTALL_CAPABILITIES" | tr ',' ' '); do \
      case "$capability" in \
        vscode|browser|desktop|docker) ;; \
        *) echo "Unknown capability '$capability' in INSTALL_CAPABILITIES" \
             "(expected one of: vscode, browser, desktop, docker)" >&2; exit 1;; \
      esac; \
    done

# --- VSCode Web ---
# EDITOR/VISUAL/GIT_EDITOR/OPENVSCODE_SERVER_ROOT are declared unconditionally:
# nothing execs `code` unless VSCode Web itself is running, so a dangling
# path when `vscode` is excluded is harmless (same reasoning as CHROME_BIN
# below, applied to VSCode).
ENV EDITOR=code \
    VISUAL=code \
    GIT_EDITOR="code --wait" \
    OPENVSCODE_SERVER_ROOT=/openhands/.openvscode-server
ARG RELEASE_TAG="openvscode-server-v1.98.2"
ARG RELEASE_ORG="gitpod-io"
# The --mount=from=builder on the extensions RUN below is unconditional at
# the Dockerfile level, so BuildKit still builds the full `builder` stage
# (the entire SDK venv) even when `vscode` is excluded from
# INSTALL_CAPABILITIES — the case check inside each RUN only decides what
# lands in THIS image, not what gets built as a dependency. Excluding vscode
# shrinks the result, not the build time.
RUN set -eux; \
    case ",$INSTALL_CAPABILITIES," in \
      *,vscode,*) ;; \
      *) echo "INSTALL_CAPABILITIES excludes vscode; skipping OpenVSCode Server"; exit 0;; \
    esac; \
    \
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

# Kept as its own RUN, separate from the tarball download above: extensions
# are actively-edited local source (unlike the pinned release tarball), so
# splitting them keeps an extension-only change from invalidating the ~100MB
# download's cache layer.
RUN --mount=type=bind,from=builder,source=/agent-server/openhands-agent-server/openhands/agent_server/vscode_extensions,target=/tmp/vscode_extensions \
    set -eux; \
    case ",$INSTALL_CAPABILITIES," in \
      *,vscode,*) ;; \
      *) exit 0;; \
    esac; \
    \
    # Trailing slashes matter: OpenVSCode Server's tarball already ships its
    # own extensions/ dir, so a bare `cp -r src dst` would nest src under the
    # existing dst instead of merging into it the way `COPY` does.
    mkdir -p ${OPENVSCODE_SERVER_ROOT}/extensions; \
    cp -r /tmp/vscode_extensions/. ${OPENVSCODE_SERVER_ROOT}/extensions/; \
    chown -R ${USERNAME}:${USERNAME} ${OPENVSCODE_SERVER_ROOT}/extensions

# --- Docker ---
RUN set -eux; \
    case ",$INSTALL_CAPABILITIES," in \
      *,docker,*) ;; \
      *) echo "INSTALL_CAPABILITIES excludes docker; skipping Docker Engine"; exit 0;; \
    esac; \
    \
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
    rm -rf /var/lib/apt/lists/*; \
    \
    # Configure Docker daemon with MTU 1450 to prevent packet fragmentation issues
    mkdir -p /etc/docker; \
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
# `desktop` (tigervnc/xfce4/dbus-x11/novnc/websockify) and `browser`
# (chromium) are independently selectable: BrowserToolSet needs Chromium but
# not the desktop stack, so bundling them forced every browser consumer to
# also carry XFCE/VNC/noVNC. See OpenHands/software-agent-sdk#4645.
ENV NOVNC_WEB=/usr/share/novnc \
    NOVNC_PORT=8002 \
    DISPLAY=:1 \
    VNC_GEOMETRY=1280x800 \
    CHROME_BIN=/usr/bin/chromium \
    PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium \
    CHROMIUM_FLAGS="--no-sandbox --disable-dev-shm-usage --disable-gpu"

# NOVNC_WEB/CHROME_BIN/PUPPETEER_EXECUTABLE_PATH above are declared
# unconditionally regardless of which capabilities are selected. Nothing in
# this repo trusts them as an availability signal — browser_use's chromium
# detection (openhands-tools/openhands/tools/browser_use/impl.py) does its
# own filesystem probe of standard install paths — so when `desktop`/
# `browser` is excluded these simply point at paths that don't exist,
# rather than causing a false "available" signal downstream.
RUN --mount=type=bind,source=openhands-agent-server/openhands/agent_server/docker/wallpaper.svg,target=/tmp/wallpaper.svg \
    set -eux; \
    WANT_DESKTOP=0; \
    WANT_BROWSER=0; \
    case ",$INSTALL_CAPABILITIES," in *,desktop,*) WANT_DESKTOP=1;; esac; \
    case ",$INSTALL_CAPABILITIES," in *,browser,*) WANT_BROWSER=1;; esac; \
    PACKAGES=""; \
    if [ "$WANT_DESKTOP" = "1" ]; then \
      PACKAGES="$PACKAGES tigervnc-standalone-server xfce4 dbus-x11 novnc websockify"; \
    fi; \
    if [ "$WANT_BROWSER" = "1" ]; then \
      PACKAGES="$PACKAGES $(if grep -q "ubuntu" /etc/os-release; then echo "chromium-browser"; else echo "chromium"; fi)"; \
    fi; \
    if [ -n "$PACKAGES" ]; then \
      apt-get update; \
      apt-get install -y --no-install-recommends $PACKAGES; \
      apt-get clean; rm -rf /var/lib/apt/lists/*; \
    fi; \
    if [ "$WANT_DESKTOP" = "1" ]; then \
      chown -R ${USERNAME}:${USERNAME} ${NOVNC_WEB}; \
      # Override default XFCE wallpaper
      mkdir -p /usr/share/backgrounds/xfce; \
      cp /tmp/wallpaper.svg /usr/share/backgrounds/xfce/xfce-shapes.svg; \
      chown ${USERNAME}:${USERNAME} /usr/share/backgrounds/xfce/xfce-shapes.svg; \
    fi

USER ${USERNAME}
WORKDIR /
ENV OH_ENABLE_VNC=false
ENV LOG_JSON=true
# The ACP CLIs run without the user's host home here, so OpenHands supplies the
# managed skill catalog they cannot read for themselves (#4019).
ENV OH_ACP_SKILL_SOURCING=openhands_managed
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
ENTRYPOINT ["tini", "--", "/agent-server/.venv/bin/python", "-m", "openhands.agent_server"]

FROM base-image-minimal AS source-minimal
ARG USERNAME
COPY --chown=${USERNAME}:${USERNAME} --from=builder /agent-server /agent-server
ENTRYPOINT ["tini", "--", "/agent-server/.venv/bin/python", "-m", "openhands.agent_server"]

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
ENTRYPOINT ["tini", "--", "/usr/local/bin/openhands-agent-server"]

FROM base-image-minimal AS binary-minimal
ARG USERNAME
COPY --chown=${USERNAME}:${USERNAME} --from=binary-builder /agent-server/dist/openhands-agent-server /usr/local/bin/openhands-agent-server
RUN chmod +x /usr/local/bin/openhands-agent-server
# Fix library path to use system GCC libraries instead of bundled ones
ENV LD_LIBRARY_PATH=/usr/lib/aarch64-linux-gnu:/usr/lib:/usr/lib/x86_64-linux-gnu:$LD_LIBRARY_PATH
ENTRYPOINT ["tini", "--", "/usr/local/bin/openhands-agent-server"]
