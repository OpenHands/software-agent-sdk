#!/bin/bash
# Build script for custom agent server image with custom tools
#
# This script builds a custom Docker image that extends the base agent server
# image to include your custom tools.
#
# Usage:
#   ./build_custom_image.sh [TAG]
#
# Arguments:
#   TAG: Optional custom tag for the image (default: custom-agent-server:latest)

set -e

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Default tag
TAG="${1:-custom-agent-server:latest}"

# Base image to extend
BASE_IMAGE="${BASE_IMAGE:-ghcr.io/openhands/agent-server:latest-python}"

echo "🐳 Building custom agent server image..."
echo "📦 Base image: $BASE_IMAGE"
echo "🏷️  Tag: $TAG"
echo ""

# Build the image
docker build \
  -t "$TAG" \
  -f "$SCRIPT_DIR/Dockerfile" \
  --build-arg BASE_IMAGE="$BASE_IMAGE" \
  "$SCRIPT_DIR"

echo ""
echo "✅ Custom agent server image built successfully!"
echo "🏷️  Image tag: $TAG"
echo ""
echo "To use this image:"
echo "  1. Run it directly:"
echo "     docker run -p 8000:8000 $TAG"
echo ""
echo "  2. Use in SDK with DockerWorkspace:"
echo "     with DockerWorkspace(server_image='$TAG', host_port=8010) as workspace:"
echo "         # your code"
echo ""
echo "  3. Push to registry (optional):"
echo "     docker tag $TAG your-registry/$TAG"
echo "     docker push your-registry/$TAG"
