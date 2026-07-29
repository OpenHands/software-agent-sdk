# syntax=docker/dockerfile:1.7
ARG BASE_IMAGE=nikolaik/python-nodejs:python3.13-nodejs22-slim
ARG USERNAME=openhands
ARG UID=10001
ARG GID=10001
ARG PORT=8000
ARG ENABLE_VERTEX=0

####################################################################################
FROM python:3.13-bookworm AS builder
ARG USERNAME UID GID ENABLE_VERTEX
ENV UV_PROJECT_ENVIRONMENT=/agent-server/.venv
ENV UV_PYTHON_INSTALL_DIR=/agent-server/uv-managed-python

COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /uvx /bin/

RUN groupadd -g ${GID} ${USERNAME} \
 && useradd -m -u ${UID} -g ${GID} -s /usr/sbin/nologin ${USERNAME} \
 && mkdir -p /agent-server/uv-managed-python \
 && chown -R ${USERNAME}:${USERNAME} /agent-server
USER ${USERNAME}
WORKDIR /agent-server
COPY --chown=${USERNAME}:${USERNAME} pyproject.toml uv.lock README.md LICENSE ./
COPY --chown=${USERNAME}:${USERNAME} openhands-sdk ./openhands-sdk
COPY --chown=${USERNAME}:${USERNAME} openhands-tools ./openhands-tools
COPY --chown=${USERNAME}:${USERNAME} openhands-workspace ./openhands-workspace
COPY --chown=${USERNAME}:${USERNAME} openhands-agent-server ./openhands-agent-server
COPY --chown=${USERNAME}:${USERNAME} .agents ./.agents
RUN --mount=type=cache,target=/home/${USERNAME}/.cache,uid=${UID},gid=${GID} \
    EXTRA_FLAGS=""; \
    if [ "$ENABLE_VERTEX" = "1" ]; then EXTRA_FLAGS="--extra vertex"; fi; \
    uv python install 3.13 && \
    uv venv --python-preference only-managed --python 3.13 .venv && \
    uv sync --frozen --no-editable --managed-python --extra boto3 $EXTRA_FLAGS && \
    readlink -f .venv/bin/python | grep -q '^/agent-server/uv-managed-python/'

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
# Pyromind knowledge base
####################################################################################
FROM debian:bookworm-slim AS knowledge-sync
ARG USERNAME UID GID
RUN groupadd -g ${GID} ${USERNAME} \
 && useradd -m -u ${UID} -g ${GID} -s /usr/sbin/nologin ${USERNAME}
WORKDIR /sync
COPY --chown=${USERNAME}:${USERNAME} knowledge ./knowledge
RUN set -eux; \
    for path in \
        knowledge/basic \
        knowledge/jupyterlab \
        knowledge/nodes \
        knowledge/sdk \
        knowledge/studio; do \
        test -d "$path" || { \
            echo "Missing required build asset directory: $path" >&2; \
            exit 1; \
        }; \
    done; \
    test -f knowledge/dataset_processing_workflow.py || { \
        echo "Missing required build asset file: knowledge/dataset_processing_workflow.py" >&2; \
        exit 1; \
    }

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
            tini apt-transport-https gnupg lsb-release xz-utils \
            apparmor apparmor-utils bubblewrap; \
        rm -rf /var/lib/apt/lists/*; \
    elif command -v apk >/dev/null 2>&1; then \
        apk add --no-cache \
            bash ca-certificates curl wget sudo git jq tmux tar build-base \
            coreutils util-linux procps findutils grep sed tini gnupg shadow xz \
            bubblewrap; \
    elif command -v microdnf >/dev/null 2>&1; then \
        microdnf install -y \
            bash ca-certificates curl wget sudo git jq tmux tar make gcc gcc-c++ \
            coreutils util-linux procps-ng findutils grep sed shadow-utils \
            gnupg2 xz bubblewrap; \
        microdnf clean all; \
    elif command -v dnf >/dev/null 2>&1; then \
        dnf install -y \
            bash ca-certificates curl wget sudo git jq tmux tar make gcc gcc-c++ \
            coreutils util-linux procps-ng findutils grep sed shadow-utils \
            gnupg2 xz bubblewrap; \
        dnf clean all; \
    elif command -v yum >/dev/null 2>&1; then \
        yum install -y \
            bash ca-certificates curl wget sudo git jq tmux tar make gcc gcc-c++ \
            coreutils util-linux procps-ng findutils grep sed shadow-utils \
            gnupg2 xz bubblewrap; \
        yum clean all; \
    elif command -v zypper >/dev/null 2>&1; then \
        zypper --non-interactive install --no-recommends \
            bash ca-certificates curl wget sudo git jq tmux tar make gcc gcc-c++ \
            coreutils util-linux procps findutils grep sed shadow gpg2 xz \
            bubblewrap; \
        zypper clean --all; \
    else \
        echo "Unsupported base image: no known package manager found" >&2; \
        exit 1; \
    fi; \
    grep -Eq "^[^:]*:[^:]*:${GID}:" /etc/group || groupadd -g "${GID}" "${USERNAME}"; \
    grep -Eq "^${USERNAME}:" /etc/passwd || \
        useradd -m -u "${UID}" -g "${GID}" -s /bin/bash "${USERNAME}"; \
    usermod -aG sudo "${USERNAME}" 2>/dev/null || true; \
    echo "${USERNAME} ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers;


# Pre-load the terminal AppArmor profile so `aa-exec -p openhands-agent-terminal`
# works at runtime without CAP_MAC_ADMIN. Best-effort: if the host kernel does
# not have the AppArmor LSM active (e.g. some container runtimes), the parser
# exits non-zero but we do not fail the whole image build.
COPY --from=builder /agent-server/openhands-tools/openhands/tools/terminal/apparmor/openhands-agent-terminal.profile \
    /etc/apparmor.d/openhands-agent-terminal
RUN if command -v apparmor_parser >/dev/null 2>&1; then \
        apparmor_parser -r -W /etc/apparmor.d/openhands-agent-terminal \
            || echo "Warning: apparmor_parser failed; AppArmor LSM likely inactive on build host. Runtime aa-exec will be a no-op." >&2; \
    else \
        echo "Warning: apparmor_parser not installed; skipping profile load" >&2; \
    fi

# Pre-install ACP servers for ACPAgent support (Claude Code, Codex, Gemini CLI)
# Install Node.js 22 to a dedicated prefix so ACP packages get a modern runtime
# WITHOUT overwriting the repo-specific Node.js that test suites depend on.
# SWE-bench images ship NVM/apt-managed Node 8-14 which cannot run ACP packages.
#
# This step is best-effort: SWE-Bench Pro base images come from many distros
# and some have an old glibc (or use musl) that cannot run the upstream Node
# 22 glibc tarball. When that happens we leave $ACP_NODE_DIR empty and skip
# ACP setup so the rest of the build (and non-ACP agents) still work.

ENV ACP_NODE_DIR=/opt/acp-node
RUN set -ux; \
    mkdir -p "$ACP_NODE_DIR"; \
    ARCH=$(uname -m); \
    NARCH=""; \
    NODE_SHA256=""; \
    case "$ARCH" in \
      x86_64|amd64) NARCH=x64; NODE_SHA256=69b09dba5c8dcb05c4e4273a4340db1005abeafe3927efda2bc5b249e80437ec;; \
      aarch64|arm64) NARCH=arm64; NODE_SHA256=08bfbf538bad0e8cbb0269f0173cca28d705874a67a22f60b57d99dc99e30050;; \
    esac; \
    NODE_TARBALL=""; \
    if [ -z "$NARCH" ]; then \
      echo "Skipping ACP Node install: unsupported architecture '$ARCH'" >&2; \
    else \
      NODE_TARBALL="/tmp/node-v22.14.0-linux-${NARCH}.tar.xz"; \
      if curl -fsSL --retry 5 --retry-delay 2 --retry-connrefused "https://nodejs.org/dist/v22.14.0/node-v22.14.0-linux-${NARCH}.tar.xz" -o "$NODE_TARBALL" \
         && echo "$NODE_SHA256  $NODE_TARBALL" | sha256sum -c - \
         && tar -xJ --strip-components=1 -C "$ACP_NODE_DIR" -f "$NODE_TARBALL" \
         && "$ACP_NODE_DIR/bin/node" --version; then \
        PATH="$ACP_NODE_DIR/bin:$PATH"; \
        if "$ACP_NODE_DIR/bin/npm" install -g \
            @agentclientprotocol/claude-agent-acp@0.44.0 \
            @zed-industries/codex-acp@0.16.0 \
            @google/gemini-cli@0.46.0; then \
          # Create wrappers in /usr/local/bin that prepend ACP's Node 22 to PATH.
          # This ensures the ACP binary's #!/usr/bin/env node shebang resolves
          # to Node 22, while the repo's own node (NVM/system) stays untouched
          # for tests.
          for bin in claude-agent-acp codex-acp gemini; do \
            if [ -e "$ACP_NODE_DIR/bin/$bin" ]; then \
              printf '#!/bin/sh\nPATH="%s/bin:$PATH" exec "%s/bin/%s" "$@"\n' \
                "$ACP_NODE_DIR" "$ACP_NODE_DIR" "$bin" \
                > /usr/local/bin/"$bin"; \
              chmod +x /usr/local/bin/"$bin"; \
            fi; \
          done; \
        else \
          echo "Warning: ACP npm install failed; ACP agents will not be available on this image" >&2; \
          rm -rf "$ACP_NODE_DIR"/*; \
        fi; \
      else \
        echo "Warning: ACP Node 22 runtime is not compatible with this base image (likely older glibc or musl libc); ACP agents will not be available" >&2; \
        rm -rf "$ACP_NODE_DIR"/*; \
      fi; \
    fi; \
    rm -f "$NODE_TARBALL" 2>/dev/null || true

RUN mkdir -p /etc/claude-code && \
    echo '{"permissions":{"allow":["Edit","Read","Bash"]}}' > /etc/claude-code/managed-settings.json

COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /uvx /bin/

USER ${USERNAME}
WORKDIR /
ENV LC_ALL=C.UTF-8
ENV LANG=C.UTF-8
ENV OH_ENABLE_VNC=false
ENV LOG_JSON=true
ENV workspace_dir=/workspace
ENV WORKSPACE_DIR=/workspace
EXPOSE ${PORT}


FROM base-image-minimal AS binary-minimal
ARG USERNAME
COPY --chown=${USERNAME}:${USERNAME} --from=binary-builder /agent-server/dist/openhands-agent-server /usr/local/bin/openhands-agent-server
COPY --chown=${USERNAME}:${USERNAME} --from=builder /agent-server/.agents /agent-server/.agents
COPY --chown=${USERNAME}:${USERNAME} --from=knowledge-sync /sync/knowledge /agent-server/knowledge
RUN chmod +x /usr/local/bin/openhands-agent-server
# DataFlow venv for df_run_pipeline local trial runs
RUN python3 -m venv /opt/dataflow-venv \
    && /opt/dataflow-venv/bin/pip install --no-cache-dir open-dataflow==1.0.10
ENV DATAFLOW_PYTHON=/opt/dataflow-venv/bin/python
# Fix library path to use system GCC libraries instead of bundled ones
ENV LD_LIBRARY_PATH=/usr/lib/aarch64-linux-gnu:/usr/lib:/usr/lib/x86_64-linux-gnu:$LD_LIBRARY_PATH
ENV PYROMIND_KNOWLEDGE_BASE_PATH=/agent-server/knowledge
ENV PYROMIND_PUBLIC_READ_PATHS=/agent-server/.agents/skills
ENV PYROMIND_SKILLS_PATH=/agent-server/.agents/skills
ENTRYPOINT ["tini", "--", "/usr/local/bin/openhands-agent-server"]
