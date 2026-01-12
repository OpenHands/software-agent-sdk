#!/bin/bash
# Build script for custom base image with custom tools
#
# This script builds a custom base image that includes your custom tools.
# When used with DockerDevWorkspace(base_image=...), the agent server
# will be built on top of this image automatically.
#
# Usage:
#   ./build_custom_image.sh [TAG]
#
# Arguments:
#   TAG: Optional custom tag for the image (default: custom-base-image:latest)

set -e

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Default tag
TAG="${1:-custom-base-image:latest}"

echo "🐳 Building custom base image with custom tools..."
echo "🏷️  Tag: $TAG"
echo "📂 Build context: $SCRIPT_DIR"
echo ""

# Build the image from the example directory
# The Dockerfile just copies custom_tools into the base image
docker build \
  -t "$TAG" \
  "$SCRIPT_DIR"

echo ""
echo "✅ Custom base image built successfully!"
echo "🏷️  Image tag: $TAG"
echo ""
echo "To use this image:"
echo "  1. Use in SDK with DockerDevWorkspace:"
echo "     with DockerDevWorkspace(base_image='$TAG', host_port=8010) as workspace:"
echo "         # DockerDevWorkspace will build the agent server on top of this base image"
echo "         # your code"
echo ""
echo "  2. Push to registry (optional):"
echo "     docker tag $TAG your-registry/$TAG"
echo "     docker push your-registry/$TAG"
