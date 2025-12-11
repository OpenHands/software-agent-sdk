# Dockerfile for custom agent server with custom tools
# This Dockerfile extends a pre-built agent server image to include custom tools
#
# Usage:
#   docker build -t my-custom-agent-server:latest \
#     -f Dockerfile \
#     --build-arg BASE_IMAGE=ghcr.io/openhands/agent-server:latest-python \
#     .

ARG BASE_IMAGE=ghcr.io/openhands/agent-server:latest-python
FROM ${BASE_IMAGE}

# Copy custom tools into the Python path
# This makes them importable as "custom_tools.report_bug"
COPY custom_tools /app/custom_tools

# Add /app to PYTHONPATH so custom_tools can be imported
ENV PYTHONPATH="/app:${PYTHONPATH}"

# The base image already exposes port 8000 and has the CMD set
# No need to redeclare them
