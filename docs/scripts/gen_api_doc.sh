#!/usr/bin/env bash
set -euo pipefail

# Generate Markdown API docs from SDK docstrings into the docs repo (Mintlify)
# Usage:
#   docs/scripts/gen_api_doc.sh [OUTPUT_DIR]
# If OUTPUT_DIR is not provided, defaults to ../openhands-docs/sdk/reference

REPO_ROOT=$(cd "$(dirname "$0")/../.." && pwd)
DOCS_REPO_DEFAULT="$REPO_ROOT/../openhands-docs/sdk/reference"
OUTPUT_DIR="${1:-$DOCS_REPO_DEFAULT}"

# Ensure output dir exists and is empty
rm -rf "$OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR"

# Generate using pdoc (isolated via uvx to avoid workspace dependency conflicts)
export PYTHONPATH="$REPO_ROOT/openhands-sdk:$PYTHONPATH"
# Note: we avoid generating the top-level package in one shot because griffe2md
# may stumble on some namespace subpackages (e.g. ones without __init__.py).
# Instead we generate per-subpackage pages and build our own index below.

# Split per-top-level subpackage into individual pages for navigation clarity.
# Skip internal/non-essential namespaces that may cause issues with generators.
SKIP_PKGS=(git)
should_skip() {
  local x="$1"
  for s in "${SKIP_PKGS[@]}"; do
    if [[ "$x" == "$s" ]]; then return 0; fi
  done
  return 1
}

while IFS= read -r -d '' pkg; do
  name=$(basename "$pkg")
  if should_skip "$name"; then
    echo "[gen_api_doc] Skipping package: $name"
    continue
  fi
  PYTHONPATH="$REPO_ROOT/openhands-sdk:$PYTHONPATH" uvx --from griffe2md griffe2md "openhands.sdk.$name" -o "$OUTPUT_DIR/$name.md" \
    || { echo "[gen_api_doc] Failed to generate for $name, skipping" >&2; continue; }
done < <(find "$REPO_ROOT/openhands-sdk/openhands/sdk" -maxdepth 1 -mindepth 1 -type d ! -name '__pycache__' -print0)

# Post-process: remove __init__ module files, which aren't useful for client developers.
find "$OUTPUT_DIR" -type f -name "*.__init__.md" -delete || true
find "$OUTPUT_DIR" -type f -name "__init__.md" -delete || true

# Create a minimal README/index for the reference section
cat > "$OUTPUT_DIR/README.md" <<'MD'
# API Reference

Generated from Python docstrings. Do not edit manually. Regenerate via:

uv run docs/scripts/gen_api_doc.sh
MD

# Optionally create Mintlify-friendly index.mdx that links to generated modules
# Build a simple nav list of all md files relative to this directory
INDEX_FILE="$OUTPUT_DIR/index.mdx"
echo "# API Reference" > "$INDEX_FILE"
echo >> "$INDEX_FILE"
echo "> This section is auto-generated from docstrings. ✨" >> "$INDEX_FILE"
echo >> "$INDEX_FILE"

while IFS= read -r -d '' file; do
  rel=${file#"$OUTPUT_DIR/"}
  # Skip README and the index itself
  [[ "$rel" == "README.md" || "$rel" == "index.mdx" ]] && continue
  # Make mintlify link without .md extension
  link="${rel%.md}"
  name="${link##*/}"
  echo "- [${name}](${link})" >> "$INDEX_FILE"
done < <(find "$OUTPUT_DIR" -type f -name "*.md" -print0 | sort -z)

# Done
