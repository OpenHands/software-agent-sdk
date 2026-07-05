# Dockerfile for custom base image with custom tools
#
# This Dockerfile creates a base image that includes custom tools.
# When used with DockerDevWorkspace(base_image=..., target="binary"),
# the binary agent server will be built on top of this image automatically.
#
# Usage:
#   cd examples/02_remote_agent_server/06_custom_tool
#   docker build -t custom-base-image:latest .

FROM nikolaik/python-nodejs:python3.13-nodejs22-slim

# Copy custom tools into a directory outside the frozen binary.
COPY custom_tools /app/custom_tools

# Tell the binary agent server where to find external Python modules.
ENV OH_EXTRA_PYTHON_PATH="/app"
