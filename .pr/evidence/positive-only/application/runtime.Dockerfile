# Test-only source runtime: released OS/tools image plus locked packaged Python.
# Both variants differ only in the built SDK wheel. No live runtime patching.
FROM ghcr.io/openhands/agent-server@sha256:b43ee580c2177280c81cb807c9a559d7a14aa13b48dba5040132a6a637bb6d55
USER root
COPY requirements.txt /tmp/ohe3895/requirements.txt
RUN uv venv /opt/ohe3895 --python /usr/local/bin/python && uv pip install --python /opt/ohe3895/bin/python --require-hashes -r /tmp/ohe3895/requirements.txt
COPY openhands_agent_server-1.60.1-py3-none-any.whl openhands_tools-1.60.1-py3-none-any.whl /tmp/ohe3895/
ARG VARIANT
COPY ${VARIANT}/openhands_sdk-1.60.1-py3-none-any.whl /tmp/ohe3895/
RUN uv pip install --python /opt/ohe3895/bin/python --no-deps /tmp/ohe3895/*.whl && printf '#!/bin/sh\nexec /opt/ohe3895/bin/python -m openhands.agent_server "$@"\n' > /usr/local/bin/openhands-agent-server && chmod 755 /usr/local/bin/openhands-agent-server
ENV PATH="/opt/ohe3895/bin:${PATH}"
ENTRYPOINT ["tini", "--", "/usr/local/bin/openhands-agent-server"]
