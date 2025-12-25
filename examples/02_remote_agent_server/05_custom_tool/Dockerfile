# Dockerfile for custom base image with custom tools
#
# This Dockerfile creates a base image that includes custom tools.
# When used with DockerDevWorkspace(base_image=...), the agent server
# will be built on top of this image automatically.
#
# Usage:
#   cd examples/02_remote_agent_server/05_custom_tool
#   docker build -t custom-base-image:latest .

FROM nikolaik/python-nodejs:python3.12-nodejs22

# Copy custom tools into the Python path
COPY custom_tools /app/custom_tools

# Add /app to PYTHONPATH so custom_tools can be imported
ENV PYTHONPATH="/app:${PYTHONPATH}"
